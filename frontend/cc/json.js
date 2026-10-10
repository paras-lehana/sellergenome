// JSON rendering: a collapsible tree (with JSON-pointer highlight) and flat syntax-highlighted text.
import { esc } from './ui.js';

export function highlight(value) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return esc(text ?? '').replace(/(&quot;(?:\\.|[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false)\b|\bnull\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g, (m, str, colon, bool) => {
    if (str) return colon ? `<span class="j-key">${str}</span><span class="j-punc">${colon}</span>` : `<span class="j-str">${str}</span>`;
    if (bool) return `<span class="j-bool">${m}</span>`;
    if (m === 'null') return `<span class="j-null">${m}</span>`;
    return `<span class="j-num">${m}</span>`;
  });
}

function scalar(v) {
  if (v === null) return '<span class="j-null">null</span>';
  if (typeof v === 'string') return `<span class="j-str">"${esc(v.length > 600 ? v.slice(0, 600) + '…' : v)}"</span>`;
  if (typeof v === 'boolean') return `<span class="j-bool">${v}</span>`;
  return `<span class="j-num">${esc(v)}</span>`;
}

const escapePointer = k => String(k).replace(/~/g, '~0').replace(/\//g, '~1');

// Render a tree. Nodes on the path to `focus` (a JSON pointer) start open and the target is highlighted.
export function tree(value, focus = '', key = null, ptr = '', depth = 0) {
  const label = key === null ? '' : `<span class="j-key">${esc(typeof key === 'number' ? key : JSON.stringify(key))}</span><span class="j-punc">: </span>`;
  const isTarget = focus && ptr === focus;
  if (value === null || typeof value !== 'object') {
    return `<div class="row${isTarget ? ' hl' : ''}" data-ptr="${esc(ptr)}">${label}${scalar(value)}</div>`;
  }
  const entries = Array.isArray(value) ? value.map((v, i) => [i, v]) : Object.entries(value);
  const open = depth < 2 || (focus && (focus === ptr || focus.startsWith(ptr + '/')));
  const brackets = Array.isArray(value) ? ['[', ']'] : ['{', '}'];
  const count = `<span class="dim"> ${entries.length} ${Array.isArray(value) ? 'items' : 'keys'}</span>`;
  if (!entries.length) return `<div class="row${isTarget ? ' hl' : ''}" data-ptr="${esc(ptr)}">${label}<span class="j-punc">${brackets.join('')}</span></div>`;
  return `<details ${open ? 'open' : ''} data-ptr="${esc(ptr)}" class="${isTarget ? 'hl' : ''}"><summary>${label}<span class="j-punc">${brackets[0]}</span>${count}</summary>${
    entries.slice(0, 400).map(([k, v]) => tree(v, focus, k, `${ptr}/${escapePointer(k)}`, depth + 1)).join('')
  }${entries.length > 400 ? `<div class="row dim">… ${entries.length - 400} more</div>` : ''}<div class="j-punc" style="margin-left:-14px">${brackets[1]}</div></details>`;
}

export function treeBlock(value, focus = '') {
  return `<div class="jtree code wrap" style="white-space:normal">${tree(value, focus)}</div>`;
}
