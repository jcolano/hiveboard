// ═══════════════════════════════════════════════════
//  HIVEBOARD — SHARED UTILITIES (common.js)
//  Used by hiveboard.js and insights.js
// ═══════════════════════════════════════════════════

// ── Environment Detection ────────────────────────────
var _isLocal = (window.location.hostname === 'localhost'
  || window.location.hostname === '127.0.0.1');

var CONFIG = {
  endpoint: _isLocal
    ? window.location.origin
    : 'https://mlbackend.net/loophive',
  wsUrl: _isLocal
    ? null
    : 'wss://85g4pm5cg9.execute-api.us-east-1.amazonaws.com/production/',
  apiKey: new URLSearchParams(window.location.search).get('apiKey')
    || localStorage.getItem('hiveboard_api_key')
    || (_isLocal ? 'hb_live_dev000000000000000000000000000000' : ''),
  pollInterval: 5000,
  maxStreamEvents: 50,
  refreshInterval: 30000,
};

// Persist API key to localStorage when resolved from URL param (P3-09-W1 fix)
if (CONFIG.apiKey && new URLSearchParams(window.location.search).get('apiKey')) {
  localStorage.setItem('hiveboard_api_key', CONFIG.apiKey);
}

// In production, if no API key, redirect to login
if (!_isLocal && !CONFIG.apiKey) {
  window.location.href = '/login.html';
}

// ── Formatting Helpers ─────────────────────────────

function hbClass(seconds) {
  if (seconds == null) return 'hb-dead';
  return seconds < 60 ? 'hb-fresh' : seconds < 300 ? 'hb-stale' : 'hb-dead';
}

function hbText(seconds) {
  if (seconds == null) return '\u2014';
  return seconds < 60 ? seconds + 's ago' : Math.floor(seconds / 60) + 'm ago';
}

function fmtTokens(n) {
  if (n == null) return '\u2014';
  return n >= 1000 ? (n / 1000).toFixed(1) + 'K' : String(n);
}

function fmtDuration(ms) {
  if (ms == null) return '\u2014';
  if (ms < 1000) return ms + 'ms';
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
  return Math.floor(ms / 60000) + 'm' + Math.round((ms % 60000) / 1000) + 's';
}

function fmtCost(c, costSource) {
  if (c == null) return '\u2014';
  if (costSource === 'estimated') return '~$' + c.toFixed(2);
  return '$' + c.toFixed(2);
}

function timeAgo(ts) {
  if (!ts) return '\u2014';
  var diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 5) return 'just now';
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  return Math.floor(diff / 3600) + 'h ago';
}

function escHtml(s) {
  if (s == null) return '';
  var d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

// ── Token Ratio Bar ────────────────────────────────

function tokenBarHtml(tokIn, tokOut) {
  if (tokIn == null && tokOut == null) return '';
  var tIn = tokIn || 0;
  var tOut = tokOut || 0;
  var max = Math.max(tIn, tOut, 1);
  var wIn = Math.round((tIn / max) * 40);
  var wOut = Math.round((tOut / max) * 40);
  return '<span class="token-bar-container">' +
    '<span class="token-bar in" style="width:' + wIn + 'px" title="' + tIn + ' tokens in"></span>' +
    '<span class="token-bar out" style="width:' + wOut + 'px" title="' + tOut + ' tokens out"></span>' +
    '<span class="token-label">' + fmtTokens(tIn) + '\u2192' + fmtTokens(tOut) + '</span></span>';
}

// ── API Client ─────────────────────────────────────

async function apiFetch(path, params) {
  var url = new URL(CONFIG.endpoint + path);
  if (params) {
    Object.entries(params).forEach(function(kv) {
      if (kv[1] != null) url.searchParams.set(kv[0], kv[1]);
    });
  }
  try {
    var resp = await fetch(url.toString(), {
      headers: { 'Authorization': 'Bearer ' + CONFIG.apiKey },
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return await resp.json();
  } catch (err) {
    console.warn('API fetch failed:', path, err.message);
    showToast('API error: ' + path + ' \u2014 ' + err.message, true);
    return null;
  }
}

// ── Toast Notifications ────────────────────────────

function showToast(msg, isError) {
  var container = document.getElementById('toastContainer');
  if (!container) return;
  var toast = document.createElement('div');
  toast.className = 'toast' + (isError ? ' error' : '');
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(function() { toast.remove(); }, 4000);
}
