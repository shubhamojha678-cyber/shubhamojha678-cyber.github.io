/* Rajasthan High Court cause-list search - client. No framework, no network calls except
   same-origin JSON. Party names are rendered here only, from data/ (never as static pages). */
(function () {
  'use strict';
  var CONFIRM = 'always confirm on hcraj.nic.in';
  var SEATS = ['jdp', 'jp'];
  var SN = { jdp: 'Jodhpur', jp: 'Jaipur' };
  var SFULL = { jdp: 'Jodhpur (Principal Seat)', jp: 'Jaipur Bench' };
  var TN = { D: 'Daily', S: 'Supplementary' };
  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var HON = { ADV: 1, ADVOCATE: 1, MR: 1, MRS: 1, MS: 1, MISS: 1, SH: 1, SHRI: 1, SMT: 1, SUSHRI: 1, DR: 1, KUM: 1, KUMARI: 1, SR: 1, LD: 1 };
  var CASE_Q = /^\s*([A-Za-z][A-Za-z0-9.()_\s-]*?)?\s*[\/\\-]?\s*(\d{1,7})\s*(?:[\/\\-]|\s+OF\s+|\s+)\s*((?:19|20)\d{2}|\d{2})\s*$/i;
  var CASE_RE = /^\s*(.*?)\s*(\d+)\s*\/\s*(\d{4})\s*$/;
  var CNR_Q = /^\s*(RJHC0[12])\s*-?\s*(\d{6})\s*-?\s*(\d{4})\s*$/i;   // CNR / CINO, e.g. RJHC010541342024
  var EPOCH = Date.UTC(2025, 0, 1);
  var MAX_ROWS = 300;

  // ---- pure functions (mirrors of cldb.norm_name, search.detect, search.held_summary) ----
  // index=true (stored names) never drops a token; a query drops an honorific only when two or
  // more tokens follow it, because MR / MS / SR / DR / SH / LD are also initials ("M.S. PUROHIT").
  function normName(s, index) {
    var toks = String(s == null ? '' : s).toUpperCase().split(/[^A-Z0-9]+/).filter(function (t) { return t; }), out = [], run = '', i, t;
    for (i = 0; i < toks.length; i++) {
      t = toks[i];
      if (!index && HON[t] === 1 && toks.length - i - 1 >= 2) continue;
      if (t.length === 1 && t >= 'A' && t <= 'Z') { run += t; continue; }
      if (run) { out.push(run); run = ''; }
      out.push(t);
    }
    if (run) out.push(run);
    return out.join(' ');
  }
  function fullYear(y) {             // 25 -> 2025; a two-digit year later than next year is 19xx
    if (y >= 100) return y;
    var yy = new Date(Date.now() + 19800000).getUTCFullYear() % 100;
    return y <= yy + 1 ? 2000 + y : 1900 + y;
  }
  function detect(q) {
    var m = CASE_Q.exec(q);
    if (m) return { kind: 'case', typ: (m[1] || '').toUpperCase().replace(/^[\s\/\\-]+|[\s\/\\-]+$/g, '').replace(/\bNO\.?\s*$/, '').replace(/[^A-Z0-9]/g, ''), num: parseInt(m[2], 10), year: fullYear(parseInt(m[3], 10)) };
    return { kind: 'name', norm: normName(q) };
  }
  function caseParts(cn) {
    var m = CASE_RE.exec(cn || '');
    if (!m) return { typ: '', num: null, year: null };
    return { typ: m[1].toUpperCase().replace(/[^A-Z0-9]/g, ''), num: parseInt(m[2], 10), year: parseInt(m[3], 10) };
  }
  function dmy(iso) { return iso.slice(8, 10) + '/' + iso.slice(5, 7) + '/' + iso.slice(0, 4); }
  function nice(iso) { return iso.slice(8, 10) + '-' + MON[parseInt(iso.slice(5, 7), 10) - 1] + '-' + iso.slice(0, 4); }
  function hhmm(ts, today) {
    if (!ts || !/^\d{4}-\d\d-\d\dT\d\d:\d\d/.test(ts)) return 'time unknown';
    var d = ts.slice(0, 10), t = ts.slice(11, 16);
    return d === today ? t : d.slice(8, 10) + '-' + MON[parseInt(d.slice(5, 7), 10) - 1] + ' ' + t;
  }
  // lists = {seat:{D:[state,rows,fetched_at],S:[...]}} for ONE date -> [text, allAvailable]
  function heldSummary(lists, today) {
    var parts = [], complete = true;
    SEATS.forEach(function (seat) {
      var bits = [];
      ['D', 'S'].forEach(function (t) {
        var v = (lists && lists[seat] && lists[seat][t]) || [null, 0, null];
        if (v[0] === 'list') bits.push(TN[t] + ' \u2713 (fetched ' + hhmm(v[2], today) + ')');
        else if (v[0] === 'no_record' && t === 'S') bits.push(TN[t] + ': none published when we checked (' + hhmm(v[2], today) + ')');
        else { complete = false; bits.push(TN[t] + ' not yet available to us'); }
      });
      parts.push(SN[seat] + ' ' + bits.join(', '));
    });
    return [parts.join('; '), complete];
  }
  function notFoundText(lists, iso, today) {
    var h = heldSummary(lists, today);
    return (h[1] ? 'Not found in the lists we hold for' : 'Not found so far \u2014 we do not hold every list yet for') +
      ' ' + dmy(iso) + ': ' + h[0] + ' \u2014 ' + CONFIRM;
  }
  function notFoundAll(covText) {
    return 'Not found in any list we hold (' + (covText || 'no lists held yet') + '). Lists before these dates are not in our database \u2014 ' + CONFIRM;
  }
  function edit1(a, b) {            // Damerau-Levenshtein distance <= 1 ?
    var la = a.length, lb = b.length, i = 0;
    if (a === b) return true;
    if (Math.abs(la - lb) > 1) return false;
    while (i < la && i < lb && a.charCodeAt(i) === b.charCodeAt(i)) i++;
    if (la === lb) return a.slice(i + 1) === b.slice(i + 1) || (a[i] === b[i + 1] && a[i + 1] === b[i] && a.slice(i + 2) === b.slice(i + 2));
    return la > lb ? a.slice(i + 1) === b.slice(i) : a.slice(i) === b.slice(i + 1);
  }
  // Similar spellings: which tokens of the vocabulary are one edit away from a query word (4+ letters)?
  function fuzzyAlts(words, vocab) {
    return words.map(function (w) {
      var set = {}, n = 0;
      if (w.length >= 4) vocab.forEach(function (t) {
        if (edit1(t.slice(0, Math.max(w.length, Math.min(t.length, w.length + 1))), w)) { set[t] = 1; n++; }
      });
      return { set: set, list: Object.keys(set), n: n };
    });
  }
  // names = normalised names of one row; words = query tokens; alts = fuzzyAlts(...) or null.
  // 0 none, 1 exact/prefix, 2 glued, 3 similar spelling
  function matchName(names, words, glued, alts) {
    var i, j, k, parts, ok, hit;
    for (i = 0; i < names.length; i++) {
      parts = names[i].split(' '); ok = true;
      for (j = 0; j < words.length && ok; j++) {
        hit = false;
        for (k = 0; k < parts.length; k++) {
          if (parts[k].lastIndexOf(words[j], 0) === 0 || (alts && alts[j].set[parts[k]] === 1)) { hit = true; break; }
        }
        ok = hit;
      }
      if (ok) return alts ? 3 : 1;
    }
    if (glued && !alts) for (i = 0; i < names.length; i++) if (names[i].replace(/ /g, '').indexOf(glued) >= 0) return 2;
    return 0;
  }
  var API = { normName: normName, detect: detect, caseParts: caseParts, heldSummary: heldSummary, notFoundText: notFoundText, notFoundAll: notFoundAll, matchName: matchName, edit1: edit1, fuzzyAlts: fuzzyAlts };
  if (typeof module !== 'undefined' && module.exports) { module.exports = API; return; }
  window.CLS = API;

  // ---------------------------------------------------------------- browser part
  if (window.top !== window.self) { try { window.top.location.replace(window.self.location.href); } catch (e) { document.documentElement.style.display = 'none'; } }   // no framing (terms of use)
  var D = document, body = D.body, ROOT = body.getAttribute('data-root') || '';
  function $(id) { return D.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function istNow() { return new Date(Date.now() + 19800000); }
  function todayIso() { return istNow().toISOString().slice(0, 10); }
  function getJSON(path, ver, fresh) {
    var url = ROOT + path + (ver ? '?v=' + ver : ''), tries = 0;
    function once() {
      var ctl = window.AbortController ? new AbortController() : null;
      var tm = setTimeout(function () { if (ctl) ctl.abort(); }, 25000);
      return fetch(url, { signal: ctl ? ctl.signal : undefined, cache: fresh ? 'no-cache' : 'default' }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      }).then(function (j) { clearTimeout(tm); return j; }, function (e) {
        clearTimeout(tm);
        if (++tries < 2) return new Promise(function (ok) { setTimeout(ok, 700); }).then(once);
        throw e;
      });
    }
    return once();
  }

  // ---- stale notice + last-updated lines: every page ----
  // Amber banner when we cannot vouch for freshness: the status file did not load, the last
  // update is older than 26 h on a working day, or the "next hearing day" we knew of has passed.
  function staleCheck(st) {
    var el = $('stale'), msg = '', now = istNow();
    if (!st || !st.written_at) msg = 'Data may be out of date — the update status could not be loaded; confirm on hcraj.nic.in';
    else {
      var age = (Date.now() - Date.parse(st.written_at)) / 3600000;
      if ((age > 26 && now.getUTCDay() !== 0) || age > 50 || (st.next_hearing_day && todayIso() > st.next_hearing_day))
        msg = 'Data may be out of date — last updated ' + nice(st.written_at.slice(0, 10)) + ' ' + st.written_at.slice(11, 16) + ' IST; confirm on hcraj.nic.in';
    }
    window.__stale = msg;
    if (el && msg) { el.hidden = false; el.textContent = msg; }
    var lab = D.querySelectorAll('[data-day]'), i, t = todayIso();
    for (i = 0; i < lab.length; i++) if (lab[i].getAttribute('data-day') === t) lab[i].textContent = ' (today)';
    if (window.__onStale) window.__onStale();
  }
  function listLine(e, today) {
    var what = TN[e.type] + ' list for ' + dmy(e.hearing_date) + ': ';
    if (e.state === 'list') return what + '<b>held</b>, ' + e.rows + ' entries, fetched ' + esc(hhmm(e.last_success_at, today));
    if (e.state === 'no_record' && e.type === 'S') return what + 'none published when we checked (' + esc(hhmm(e.last_success_at, today)) + ')';
    return what + '<b>not yet available to us</b>';
  }
  function renderUpdated(st) {
    var el = $('upd');
    if (!el || !st || !st.seats) return;
    var today = todayIso(), h = '';
    SEATS.forEach(function (s) {
      var se = st.seats[s]; if (!se) return;
      h += '<li><b>' + SN[s] + '</b> \u2014 ' + (se.lists || []).map(function (e) { return listLine(e, today); }).join(' \u00b7 ') + '</li>';
    });
    el.innerHTML = h + '<li>Checked ' + esc(hhmm(st.written_at, today)) + ' IST</li>';
  }
  var BOOT = window.__boot || {};
  (BOOT.status || getJSON('status.json', '', true)).then(function (st) { BOOT.statusVal = st; staleCheck(st); renderUpdated(st); }, function () { staleCheck(null); });
  if (body.getAttribute('data-page') !== 'home') return;

  // ---------------------------------------------------------------- home
  var meta = null, days = {}, shards = {}, runId = 0, shown = 50, liveT = 0, fuzzT = 0, keyAt = 0;
  var form = $('f'), qIn = $('q'), whenSel = $('when'), dateIn = $('date'), seatSel = $('seat'), out = $('res'), state = $('state'), countEl = $('count');
  var PAGE = 50;
  var PH0 = out.innerHTML, cnShards = {}, cnrCtx = null;      // PH0 = the picture guide shown before a search

  function prepDay(day) {            // flatten once + case-number map; names are indexed right after
    var rows = [], byCase = {};
    SEATS.forEach(function (seat) {
      var sd = day.seats && day.seats[seat];
      if (!sd) return;
      sd.courts.forEach(function (c, ci) {
        c.r.forEach(function (r) {
          var p = caseParts(r[1]), e = { seat: seat, ci: ci, c: c, r: r, typ: p.typ, num: p.num, year: p.year, n: null, j: '' }, k = p.num + '/' + p.year;
          rows.push(e);
          (byCase[k] || (byCase[k] = [])).push(e);
        });
      });
    });
    day._rows = rows; day._case = byCase; day._idx = 0;
    return day;
  }
  function rowNames(e) {
    if (e.n) return e.n;
    var r = e.r, raw = [r[2] || '', r[3] || ''].concat(String(r[4] || '').split(';'), String(r[5] || '').split(';')), n = [], i, v;
    for (i = 0; i < raw.length; i++) { v = normName(raw[i], true); if (v) n.push(v); }
    e.n = n; e.j = n.join('|');
    return n;
  }
  function vocabOf(day) {            // every distinct name token of a day, built once
    if (day._vocab) return day._vocab;
    var seen = {}, rows = day._rows, i, k, parts;
    for (i = 0; i < rows.length; i++) { parts = rowNames(rows[i]).join(' ').split(' '); for (k = 0; k < parts.length; k++) seen[parts[k]] = 1; }
    day._vocab = Object.keys(seen);
    return day._vocab;
  }
  function indexDay(day, done) {     // build the name index once, in short slices: typing never freezes
    function slice() {
      var rows = day._rows, end = Math.min(rows.length, day._idx + 1200);
      for (; day._idx < end; day._idx++) rowNames(rows[day._idx]);
      if (day._idx < rows.length) setTimeout(slice, 0); else { vocabOf(day); if (done) done(); }
    }
    slice();
  }
  function loadDay(iso) {
    if (days[iso]) return days[iso];
    if (!meta.days[iso]) return Promise.resolve(null);
    var pre = BOOT.days && BOOT.days[iso + '?' + meta.days[iso]];
    days[iso] = (pre || getJSON('data/d/cl_' + iso + '.json', meta.days[iso])).then(function (d) {
      if (!d || d.date !== iso || !d.seats) throw new Error('bad day file');
      return prepDay(d);
    });
    days[iso].catch(function () { delete days[iso]; });
    return days[iso];
  }
  function defaultDates() {
    var t = todayIso(), a = meta.site_dates.filter(function (d) { return d >= t; }).slice(0, 2);
    return a.length ? a : meta.site_dates.slice(-1);
  }
  function scope() {
    var w = whenSel.value, sd = meta.site_dates, t = todayIso(), k;
    if (w === 'date') return { dates: dateIn.value ? [dateIn.value] : defaultDates(), one: true };
    if (/^\d+$/.test(w)) {
      k = new Date(Date.parse(t) - (parseInt(w, 10) - 1) * 86400000).toISOString().slice(0, 10);
      return { dates: sd.filter(function (d) { return d >= k; }).reverse(), range: true };
    }
    if (w === 'all') return { dates: [], all: true };
    return { dates: defaultDates(), def: true };
  }
  function searchDay(day, q, seat, fuzzy) {
    var hits = [], rows = day._rows, i, j, e, words, glued, m, ok;
    if (q.kind === 'case') {
      hits = (day._case[q.num + '/' + q.year] || []).filter(function (x) { return !seat || x.seat === seat; });
      if (q.typ) { m = hits.filter(function (x) { return x.typ === q.typ; }); if (m.length) return { hits: m }; return { hits: hits, loose: hits.length > 0 }; }
      return { hits: hits };
    }
    words = q.norm.split(' '); glued = words.length > 1 ? words.join('') : '';
    var alts = fuzzy ? fuzzyAlts(words, vocabOf(day)) : null, a, x;
    if (alts && !alts.some(function (al) { return al.n; })) return { hits: [] };
    for (i = 0; i < rows.length; i++) {
      e = rows[i];
      if (seat && e.seat !== seat) continue;
      if (!e.n) rowNames(e);
      if (!glued || alts) {                     // cheap reject: every word (or one of its look-alikes) must occur in the row
        for (ok = true, j = 0; j < words.length && ok; j++) {
          if (e.j.indexOf(words[j]) >= 0) continue;
          ok = false;
          if (alts) for (a = alts[j].list, x = 0; x < a.length; x++) if (e.j.indexOf(a[x]) >= 0) { ok = true; break; }
        }
        if (!ok) continue;
      }
      if (matchName(e.n, words, glued, alts)) hits.push(e);
    }
    return { hits: hits };
  }
  function badge(lt) { return '<span class="bd ' + ['D', 'S', 'DS'][lt] + '" title="' + ['Daily list', 'Supplementary list', 'Daily and Supplementary lists'][lt] + '">' + ['D', 'S', 'D+S'][lt] + '</span>'; }
  function courtName(n) { return /^\d+$/.test(n) ? 'Court No. ' + n : n; }
  function renderHits(hits, budget, iso) {
    var h = '', lastSeat = null, lastC = null, open = false, n = 0;
    hits.forEach(function (e) {
      if (n >= budget) return;
      n++;
      if (e.seat !== lastSeat) { if (open) { h += '</div>'; open = false; } h += '<h3 class="seat">' + SFULL[e.seat] + '</h3>'; lastSeat = e.seat; lastC = null; }
      if (e.c !== lastC) {
        if (open) h += '</div>';
        h += '<div class="court"><div class="ch"><b>' + esc(courtName(e.c.n)) + '</b>' + (e.c.s ? ' · ' + esc(e.c.s) : '') + '<span>' + esc(e.c.j) + '</span></div>';
        open = true; lastC = e.c;
      }
      var r = e.r, wm = /^with\s+(.*)$/i.exec(r[0]);
      h += '<div class="it"><div class="no">' + (wm ? '<small>with item</small>' + esc(wm[1]) : '<small>item</small>' + esc(r[0])) + '</div>' +
        '<div class="cn">' + esc(r[1]) + badge(r[6]) + '</div>' +
        (r[2] === 0 ? '<div class="pt wh">(party names withheld)</div>' : '<div class="pt">' + esc(r[2]) + ' <i>vs</i> ' + esc(r[3]) + '</div>') +
        '<div class="co">Petitioner: ' + esc(r[4] || '-') + ' · Respondent: ' + esc(r[5] || '-') + '</div>' +
        '<button class="sh" type="button" data-sh="' + esc(r[1] + ' — ' + SN[e.seat] + ' ' + courtName(e.c.n).replace('Court No.', 'Court') + ', Item ' + r[0] + ' (' + ['D', 'S', 'D+S'][r[6]] + ') on ' + dmy(iso || '') + '.') + '" data-c="' + esc(r[1]) + '">Share</button></div>';
    });
    if (open) h += '</div>';
    return h;
  }
  var printAll = null;
  function show(html, count, meta2) {
    out.innerHTML = html; countEl.textContent = count || '';
    $('tools').hidden = !out.querySelector('.it, table');
    if (meta2 !== undefined) $('pmeta').textContent = meta2;
  }
  $('print').addEventListener('click', function () { window.print(); });
  window.addEventListener('beforeprint', function () {
    if (printAll) printAll();
    $('pwhen').textContent = istNow().toISOString().slice(0, 16).replace('T', ' ') + ' IST';
  });
  var SITE = 'https://shubhamojhaandassociates.com/rhc-causelist/';
  out.addEventListener('click', function (ev) {
    var b = ev.target, t = b && b.getAttribute && b.getAttribute('data-sh');
    if (!t) return;
    var text = t + ' Source: Rajasthan HC cause list via Shubham Ojha & Associates — ' + SITE + '#q=' + encodeURIComponent(b.getAttribute('data-c'));
    function done(msg) { b.textContent = msg; setTimeout(function () { b.textContent = 'Share'; }, 1800); }
    if (navigator.share) navigator.share({ text: text }).catch(function () {});
    else if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(function () { done('Copied'); }, function () { done('Copy failed'); });
    else { window.prompt('Copy this text', text); }
  });
  var HI_CONFIRM = ' · <span lang="hi">hcraj.nic.in पर पुष्टि करें</span>';
  function nfBox(lists, iso, today) {          // same words as notFoundText(), laid out: bold headline + one bullet per seat
    var h = heldSummary(lists, today);
    return '<div class="held nf"><b>' + (h[1] ? 'Not found in the lists we hold for' : 'Not found so far — we do not hold every list yet for') + ' ' + dmy(iso) + ':</b><ul>' +
      h[0].split('; ').map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>— ' + CONFIRM + HI_CONFIRM + '</div>';
  }

  function lookupIndex(q) {
    var jobs = [];
    ['b', 'd'].forEach(function (layer) {
      var starts = meta.cx && meta.cx[layer] && meta.cx[layer].y[q.year], i, k = -1;
      if (!starts) return;
      for (i = 0; i < starts.length; i++) if (starts[i] <= q.num) k = i;
      if (k < 0) return;
      var key = layer + '/' + q.year + '-' + k;
      if (!shards[key]) { shards[key] = getJSON('data/cx/' + key + '.json', meta.cx[layer].v); shards[key].catch(function () { delete shards[key]; }); }
      jobs.push(shards[key]);
    });
    return Promise.all(jobs).then(function (list) {
      var hits = [];
      list.forEach(function (sh) {
        sh.r.forEach(function (rec) {
          if (rec[0] !== q.num) return;
          var typ = sh.t[rec[1]], i;
          for (i = 2; i < rec.length; i += 5) {
            hits.push({ num: q.num, year: q.year, typ: typ, key: typ.toUpperCase().replace(/[^A-Z0-9]/g, ''),
              date: new Date(EPOCH + rec[i] * 86400000).toISOString().slice(0, 10), seat: SEATS[rec[i + 1]], court: sh.c[rec[i + 2]], item: rec[i + 3], lt: rec[i + 4] });
          }
        });
      });
      if (q.typ) { var m = hits.filter(function (x) { return x.key === q.typ; }); if (m.length) hits = m; }
      hits.sort(function (a, b) { return a.date < b.date ? 1 : a.date > b.date ? -1 : a.seat < b.seat ? -1 : 1; });
      return hits;
    });
  }
  function cnrShard(layer, key) {   // a missing shard file (404) = no CNR of that range in this layer
    var k = layer + '/' + key;
    if (layer === 'd' && (meta.cn.dk || []).indexOf(key) < 0) return Promise.resolve(null);   // listed in meta: no 404 round trip
    if (!cnShards[k]) cnShards[k] = getJSON('data/cn/' + k + '.json', String(meta.cn[layer])).catch(function (e) {
      if (/HTTP 404/.test(e && e.message)) return null;
      delete cnShards[k]; throw e;
    });
    return cnShards[k];
  }
  function cnrLookup(cnr) {          // -> {cnr, label, seat, q} | null (not in any list we hold)
    if (!meta.cn) return Promise.resolve(null);
    var pre = cnr.slice(0, 6), serial = parseInt(cnr.slice(6, 12), 10), key = pre + '-' + cnr.slice(12) + '-' + Math.floor(serial / meta.cn.bk);
    return Promise.all([cnrShard('d', key), cnrShard('b', key)]).then(function (both) {
      var j, i, r, sh;
      for (j = 0; j < 2; j++) {                     // this month's layer first: the newest label wins
        sh = both[j];
        if (!sh) continue;
        for (i = 0; i < sh.r.length; i++) {
          r = sh.r[i];
          if (r[0] === serial) {
            var label = sh.t[r[1]];
            return { cnr: cnr, label: label, seat: pre === 'RJHC01' ? 'jdp' : 'jp',
              q: { kind: 'case', typ: label.toUpperCase().replace(/[^A-Z0-9]/g, ''), num: r[2], year: r[3] } };
          }
        }
      }
      return null;
    });
  }
  function cnrMiss(cnr) {
    show('<div class="held nf"><b>CNR ' + esc(cnr) + ' is not in the cause lists we hold</b>We learn a CNR when its case appears in a cause list we hold (' +
      (cnr.slice(0, 6) === 'RJHC01' ? 'Jodhpur, Principal Seat' : 'Jaipur Bench') + '). Try the case number instead. For the case status and orders, use Case Status on hcraj.nic.in — ' + CONFIRM +
      '.<br><span lang="hi">यह CNR हमारे पास उपलब्ध वाद सूचियों में नहीं मिला। केस नंबर से खोजें; केस की स्थिति व आदेश hcraj.nic.in के Case Status में देखें।</span></div>', 'No result in the lists searched');
  }
  function indexTable(hits, skip) {
    var rows = hits.filter(function (x) { return !skip[x.date]; });
    if (!rows.length) return '';
    var h = '<div class="card"><h2>' + esc(rows[0].num + '/' + rows[0].year) + ': ' + (Object.keys(skip).length ? 'other dates' : 'dates') + ' in our database (' + rows.length + ')</h2><div class="tw"><table><tr><th>Date</th><th>Seat</th><th>Court</th><th>Item</th><th>Type</th><th></th></tr>';
    rows.slice(0, 400).forEach(function (x) {
      h += '<tr><td class="n">' + dmy(x.date) + '</td><td>' + SN[x.seat] + '</td><td>' + esc(x.court) + '</td><td>' + esc(x.item) + '</td><td>' + esc(x.typ) + badge(x.lt) + '</td><td>' +
        (meta.days[x.date] ? '<button class="lk" type="button" data-d="' + x.date + '">details</button>' : '') + '</td></tr>';
    });
    return h + '</table></div><p class="hint">Court and item from our database. Parties and counsel are shown for the last ' + meta.site_days + ' days (“details”); older full lists are in our database but not on this site. A date missing here may still be on the court’s board — ' + CONFIRM + '.</p><p class="unoff">Unofficial compilation — not a website of the Rajasthan High Court; the court’s published cause list is the only official record.</p></div>';
  }

  // live = typed (no fuzzy pass, no history entry); otherwise an explicit search
  function run(live) {
    var raw = qIn.value.trim(), my = ++runId;
    window.__runId = my;
    clearTimeout(fuzzT);
    if (!meta) { show('', 'Loading today’s list…'); pending = true; return; }
    if (!raw) { show(PH0); window.__searchDone = my; return; }
    var q = detect(raw), sc = scope(), seat = seatSel.value, today = todayIso(), note = '';
    function hint(t) { show('<div class="held">' + t + '</div>'); window.__searchDone = my; window.__keyMs = Math.round(performance.now() - keyAt); }
    var cm = CNR_Q.exec(raw), cnrHead = '';
    if (cm) {
      var cnr = (cm[1] + cm[2] + cm[3]).toUpperCase();
      if (!cnrCtx || cnrCtx.cnr !== cnr) {
        show('', 'Looking up CNR…');
        cnrLookup(cnr).then(function (rec) {
          if (my !== runId) return;
          if (!rec) { cnrMiss(cnr); window.__searchDone = my; return; }
          cnrCtx = rec; run(live);
        }, function () {
          if (my !== runId) return;
          show('<div class="held nf">Published data is not available to us right now — ' + CONFIRM + '</div>'); window.__searchDone = my;
        });
        return;
      }
      q = cnrCtx.q; seat = cnrCtx.seat;
      if (sc.def) sc = { dates: [], all: true };          // a CNR asks for the case's history, not only today
      cnrHead = '<div class="cnr">CNR <b>' + cnr + '</b> = <b>' + esc(cnrCtx.label + ' ' + q.num + '/' + q.year) + '</b> · ' + SFULL[seat] + '</div>';
    } else if (/^\s*RJHC/i.test(raw)) {
      hint('Keep typing the 16-character CNR, for example <b>RJHC010541342024</b>. <span lang="hi">पूरा 16 अक्षरों का CNR नंबर लिखें।</span>'); return;
    }
    if (q.kind === 'name' && /\d/.test(raw)) { hint('Keep typing the case number with its year, for example <b>CW 1234/2026</b> or <b>1234/2026</b>.'); return; }
    if (q.kind === 'name' && q.norm.replace(/ /g, '').length < 3) { hint('Type at least 3 letters of a name, or a case number such as <b>CW 1234/2026</b>.'); return; }
    if (sc.all && q.kind === 'name') {
      note = 'A name search covers the full lists on this site (the last ' + meta.site_days + ' days and coming dates). Use a case number to search every date in our database.';
      sc = { dates: meta.site_dates.slice().reverse(), range: true };
    }
    var dates = sc.dates, parts = {}, done = 0, idx = null, idxErr = false, fuzzy = false, t0 = performance.now(), ms = 0;
    shown = PAGE;
    if (!live) { try { history.replaceState(null, '', '#q=' + encodeURIComponent(raw)); } catch (e) { /* ignore */ } }

    function paint(final) {
      if (my !== runId) return;
      var h = '', total = 0, budget = shown, nf = [], loose = false, skip = {};
      if (cnrHead) h += cnrHead;
      if (note) h += '<div class="warn">' + esc(note) + '</div>';
      dates.forEach(function (iso) {
        var p = parts[iso];
        skip[iso] = 1;
        if (!p) return;
        if (p.err) { h += '<h2 class="date">' + dmy(iso) + '</h2><div class="held nf">Published data is not available to us right now — ' + CONFIRM + '</div>'; return; }
        if (p.hits.length) {
          total += p.hits.length; loose = loose || p.loose;
          if (budget <= 0) return;
          h += '<h2 class="date">' + dmy(iso) + (iso === today ? ' · today' : '') + ' · ' + p.hits.length + ' result' + (p.hits.length > 1 ? 's' : '') + '</h2>';
          h += renderHits(p.hits, budget, iso); budget -= p.hits.length;
          h += '<div class="held">Lists we hold for ' + dmy(iso) + ': ' + esc(heldSummary(p.lists, today)[0]) + ' — ' + CONFIRM + '</div>';
        } else nf.push(iso);
      });
      if (total > shown) h += '<p><button class="go more" type="button" id="more">Show more (' + Math.min(shown, total) + ' of ' + total + ' shown)</button></p>';
      var head = '', count = '';
      if (!final) count = 'Searching… ' + done + ' of ' + dates.length + ' date' + (dates.length === 1 ? '' : 's') + ' checked';
      else if (total) count = total + ' result' + (total > 1 ? 's' : '');
      else if (idx && idx.length) count = idx.length + ' listing' + (idx.length > 1 ? 's' : '') + ' in our database';
      else if (!idx && wantIdx && !idxErr) count = 'Searching our database…';
      else count = 'No result in the lists searched';
      if (final && fuzzy && total) head += '<div class="warn">No exact match. Showing similar spellings.</div>';
      if (final && loose) head += '<div class="warn">No exact match for that case type; showing every case with this number and year.</div>';
      var tail = '';
      if (final && nf.length) {
        if (nf.length <= 2) nf.forEach(function (iso) {
          tail += parts[iso].old ? '<div class="held nf"><b>The full list for ' + dmy(iso) + ' is not shown on this site</b>It is in our database, but full lists here cover the last ' + meta.site_days + ' days. Search by case number with “All dates” to see the court and item — ' + CONFIRM + HI_CONFIRM + '</div>' : nfBox(parts[iso].lists, iso, today);
        });
        else {
          var inc = nf.filter(function (iso) { return !heldSummary(parts[iso].lists, today)[1]; }).length;
          tail += '<div class="held nf"><b>' + (total ? 'No match on the other ' : (inc ? 'Not found so far — we do not hold every list for the ' : 'Not found in the lists we hold for the ')) + nf.length + ' dates searched (' + dmy(nf[nf.length - 1]) + ' to ' + dmy(nf[0]) + ')' +
            (inc ? '; ' + inc + ' of these dates have a list not yet available to us' : '') + '</b>— ' + CONFIRM + HI_CONFIRM +
            '<details><summary>Lists searched, date by date</summary>' + nf.map(function (iso) { return '<p>' + dmy(iso) + ': ' + esc(heldSummary(parts[iso].lists, today)[0]) + '</p>'; }).join('') + '</details></div>';
        }
      }
      if (idx) {
        var t = indexTable(idx, skip);
        if (t) tail += t; else if (final && !total && sc.all) tail += '<div class="held nf"><b>Not found in any list we hold</b>' + esc(notFoundAll(meta.cx.cov).replace('Not found in any list we hold ', '')) + HI_CONFIRM + '</div>';
      } else if (idxErr && final) tail += '<div class="held nf">Published data is not available to us right now — ' + CONFIRM + '</div>';
      var st = BOOT.statusVal, upd = st && st.written_at ? ' · Last updated ' + st.written_at.slice(0, 16).replace('T', ' ') + ' IST' : '';
      show(head + h + tail, count, 'Search: “' + raw + '” · ' + (sc.all ? 'all dates in our database' : dates.length ? (dates.length > 2 ? dmy(dates[dates.length - 1]) + ' to ' + dmy(dates[0]) : dates.map(dmy).join(' and ')) : '') + ' · ' + (seat ? SFULL[seat] : 'Jodhpur and Jaipur') + upd);
      printAll = function () { if (total > shown) { shown = Math.min(total, MAX_ROWS); paint(true); } };
      var more = $('more');
      if (more) more.onclick = function () { shown += PAGE * 2; paint(true); };
    }

    function one(iso, fz) {
      if (!meta.days[iso]) { parts[iso] = { hits: [], lists: {}, old: meta.dates.indexOf(iso) >= 0 }; done++; return null; }
      function use(day) { var r = searchDay(day, q, seat, fz); parts[iso] = { hits: r.hits, loose: r.loose, lists: meta.lists[iso] || day.lists }; done++; }
      if (days[iso] && days[iso]._ready) { use(days[iso]._ready); return null; }       // in memory: no promise, no wait
      return loadDay(iso).then(use, function () { parts[iso] = { err: 1, hits: [] }; done++; });
    }
    function pass(fz) {
      done = 0;
      var chain = null;
      dates.forEach(function (iso) {
        if (chain) chain = chain.then(function () { if (my !== runId) return; var p = one(iso, fz); if (p) return p.then(function () { if (!fz) paint(false); }); });
        else { var p = one(iso, fz); if (p) chain = p.then(function () { if (!fz) paint(false); }); }
      });
      return chain;
    }
    function any() { return dates.some(function (d) { return parts[d] && parts[d].hits.length; }); }
    function finish() {
      if (my !== runId) return;
      ms = Math.round(performance.now() - t0); window.__lastSearchMs = ms;
      paint(true);
      window.__keyMs = Math.round(performance.now() - keyAt);
      window.__searchDone = my;
    }
    function fuzzyPass() {
      if (my !== runId || any() || q.kind !== 'name') return finish();
      fuzzy = true;
      var c = pass(true);
      if (c) c.then(finish); else finish();
    }
    var wantIdx = q.kind === 'case' && !sc.one && !sc.range, idxP = null;
    if (wantIdx) idxP = lookupIndex(q).then(function (hh) { idx = seat ? hh.filter(function (x) { return x.seat === seat; }) : hh; }, function () { idxErr = true; });
    var c = pass(false);
    function afterExact() {
      if (my !== runId) return;
      if (q.kind === 'name' && !any()) {
        if (live) { finish(); fuzzT = setTimeout(function () { if (my === runId) fuzzyPass(); }, 350); return; }
        return fuzzyPass();
      }
      finish();
    }
    if (!c) { afterExact(); if (idxP) idxP.then(function () { if (my === runId) paint(true); }); return; }      // everything was in memory: answered in this tick
    show('', 'Searching…');
    Promise.all([c, idxP]).then(afterExact);
  }

  out.addEventListener('click', function (ev) {
    var d = ev.target && ev.target.getAttribute && ev.target.getAttribute('data-d');
    if (d) { whenSel.value = 'date'; dateIn.hidden = false; dateIn.value = d; run(false); window.scrollTo(0, form.offsetTop); }
  });
  whenSel.addEventListener('change', function () { dateIn.hidden = whenSel.value !== 'date'; if (qIn.value.trim()) run(false); });
  dateIn.addEventListener('change', function () { if (qIn.value.trim()) run(false); });
  seatSel.addEventListener('change', function () { if (qIn.value.trim()) run(false); });
  form.addEventListener('submit', function (ev) { ev.preventDefault(); clearTimeout(liveT); keyAt = performance.now(); run(false); });
  qIn.addEventListener('input', function () {          // results as you type
    keyAt = performance.now();
    window.__searchDone = 0;                           // pending until this keystroke is answered
    clearTimeout(liveT);
    liveT = setTimeout(function () { run(true); }, 100);
  });
  var pending = false;

  (BOOT.meta || getJSON('data/meta.json', '', true)).then(function (m) {
    meta = m;
    if (meta.site_dates.length) { dateIn.min = meta.site_dates[0]; dateIn.max = meta.site_dates[meta.site_dates.length - 1]; dateIn.value = defaultDates()[0]; }
    var dd = defaultDates(), today = todayIso(), left = dd.length;
    if ($('scope')) $('scope').textContent = 'Default search: ' + dd.map(function (d) { return dmy(d) + (d === today ? ' (today)' : ''); }).join(' and ') + ', both seats. Name search covers lists from ' + dmy(meta.site_dates[0] || today) + '; older lists are searchable by case number (choose “All dates”).';
    window.__ready = performance.now();
    try { var hq = /[#&]q=([^&]*)/.exec(location.hash); if (hq && !qIn.value) qIn.value = decodeURIComponent(hq[1]); } catch (e) { /* a malformed link must not break the page */ }
    var loaded = false;
    function label() {                        // never green while we cannot vouch for freshness
      loaded = true;
      if (window.__stale === undefined) return;            // wait for the status answer
      if (window.__stale) { state.className = 'state'; state.textContent = 'Lists loaded (' + dd.map(dmy).join(' and ') + '), but they may be out of date — confirm on hcraj.nic.in.'; return; }
      state.className = 'state ok';
      state.textContent = 'Ready — lists loaded: ' + dd.map(dmy).join(' and ') + '. Results appear as you type.';
    }
    window.__onStale = function () { if (loaded) label(); };
    if (!left) label();
    dd.forEach(function (d) {      // today + next hearing day: fetched in parallel at page open, indexed once
      loadDay(d).then(function (day) {
        indexDay(day, function () {
          days[d]._ready = day;
          window.__warm = (window.__warm || 0) + 1;
          if (--left === 0) { window.__dataReady = performance.now(); label(); if (qIn.value.trim()) run(!pending); pending = false; }
        });
      }, function () {
        if (--left === 0) { window.__dataReady = performance.now(); if (qIn.value.trim()) run(false); }
        state.className = 'state'; state.textContent = 'A list could not be loaded just now. Search still works for what we could load — ' + CONFIRM + '.';
      });
    });
    if (qIn.value.trim()) { pending = true; run(false); }
  }, function () {
    state.textContent = '';
    show('<div class="held nf">Published data is not available to us right now — ' + CONFIRM + '</div>');
    window.__ready = performance.now();
  });
})();
