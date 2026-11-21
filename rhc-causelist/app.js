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
  function pesc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function courtName(n) { return /^\d+$/.test(n) ? 'Court No. ' + n : n; }
  var DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  function wnice(iso) { return DOW[new Date(iso + 'T00:00:00Z').getUTCDay()] + ' ' + nice(iso); }
  function itemText(it, hi) { return /^with/i.test(it) ? pesc(String(it).replace(/^with\s*/i, hi ? 'आइटम के साथ: ' : 'with item ')) : (hi ? 'आइटम ' : 'Item ') + pesc(it); }
  // o = {today, hi, held: 'Jodhpur 25-Nov-2026' (latest date of which we hold a list), saved, link: fn(listing) -> href or ''}
  function whenCard(hh, o) {         // hh = every listing we hold of ONE case, newest first
    function T(en, hi) { return o.hi ? hi : en; }
    var HI = o.hi, saved = o.saved, today = o.today, fut = hh.filter(function (x) { return x.date >= today; }).reverse(), past = hh.filter(function (x) { return x.date < today; }), h;
    var tent = '<span class="wn">' + T('Actual listing in the published cause list — tentative / next dates are not shown.', 'प्रकाशित वाद सूची की वास्तविक लिस्टिंग — संभावित (टेंटेटिव) / अगली तारीखें नहीं दिखाई जातीं।') + '</span>';
    if (fut.length) {
      h = '<b class="wl">' + T('Next listed', 'अगली लिस्टिंग') + ':</b>' + tent + fut.slice(0, 6).map(function (x) {
        var ct = pesc(courtName(x.court)), href = o.link ? o.link(x) : '';
        return '<span class="wr"><b>' + wnice(x.date) + '</b>' + (x.date === today ? T(' (today)', ' (आज)') : '') + ' · ' + SN[x.seat] + ' · ' +
          (href ? '<a href="' + pesc(href) + '">' + ct + '</a>' : ct) + ' · ' + itemText(x.item, HI) + ' · ' +
          (o.part && o.part(x) ? pesc(o.part(x)) + (x.lt === 2 ? (HI ? ' (सप्लीमेंट्री में भी)' : ' (also in Supplementary)') : '') : (HI ? ['डेली लिस्ट', 'सप्लीमेंट्री लिस्ट', 'डेली + सप्लीमेंट्री लिस्ट'] : ['Daily list', 'Supplementary list', 'Daily + Supplementary lists'])[x.lt]) + '</span>';
      }).join('') + (fut.length > 6 ? '<span class="wr">+' + (fut.length - 6) + '</span>' : '');
    } else {
      h = '<b class="wl">' + T('Not in any cause list we hold for today or a coming date', 'आज या आगे की तारीख की किसी उपलब्ध वाद सूची में अभी नहीं है') + '</b>' + tent + '<span class="wr">' +
        T('Lists held up to: ', 'हमारे पास सूचियाँ: ') + (o.held || T('none yet', 'अभी कोई नहीं')) + T('. ', ' तक। ') +
        (past.length ? T('Last listed: ', 'पिछली बार सूची में: ') + nice(past[0].date) + ', ' + pesc(courtName(past[0].court)) + ', ' + itemText(past[0].item, HI) + T('. ', '। ') : T('No earlier listing in the lists we hold. ', 'हमारे पास उपलब्ध सूचियों में पहले की कोई लिस्टिंग नहीं। ')) + '</span><span class="wr">' +
        T('The court usually publishes the list for a date the evening before. ', 'कोर्ट किसी तारीख की सूची सामान्यतः एक शाम पहले जारी करती है। ') +
        (saved ? T('This case is saved ★: it will show here as soon as we hold that list.', 'यह केस ★ सेव है: वह सूची हमारे पास आते ही यहाँ दिखेगा।') :
          T('Save ★ and it will show in My list as soon as we hold that list.', '★ सेव करें — वह सूची हमारे पास आते ही केस “मेरी सूची” में दिखेगा।')) + '</span>';
    }
    return h + '<span class="wc">' + T('From the published cause lists we hold — not the case status. The court’s own next date is under Case Status on hcraj.nic.in; ' + CONFIRM + '.',
      'यह हमारे पास उपलब्ध प्रकाशित वाद सूचियों से है — केस स्टेटस नहीं। कोर्ट की दी हुई अगली तारीख hcraj.nic.in के Case Status में देखें; वहीं पुष्टि अवश्य करें।') + '</span>';
  }
  var API = { whenCard: whenCard, normName: normName, detect: detect, caseParts: caseParts, heldSummary: heldSummary, notFoundText: notFoundText, notFoundAll: notFoundAll, matchName: matchName, edit1: edit1, fuzzyAlts: fuzzyAlts };
  if (typeof module !== 'undefined' && module.exports) { module.exports = API; return; }
  window.CLS = API;

  // ---------------------------------------------------------------- browser part
  if (window.top !== window.self) { try { window.top.location.replace(window.self.location.href); } catch (e) { document.documentElement.style.display = 'none'; } }   // no framing (terms of use)
  var D = document, body = D.body, ROOT = body.getAttribute('data-root') || '';
  function $(id) { return D.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function istNow() { return new Date(Date.now() + 19800000); }
  function todayIso() { return istNow().toISOString().slice(0, 10); }
  var BOOT = window.__boot || {}, HI = /^hi/.test(body.getAttribute('data-lang') || '');
  function T(en, hi) { return HI ? hi : en; }
  // ---- offline copy: the service worker marks a SAVED answer with the time it was saved ----
  var offAt = BOOT.off || 0, staleMsg = '';
  function paintStale() {
    var el = $('stale'), msg = staleMsg, t;
    if (offAt) {
      t = new Date(offAt + 19800000).toISOString();
      msg = T('Offline — showing the copy saved at ', 'ऑफ़लाइन — सहेजी गई प्रति दिखाई जा रही है, समय: ') + nice(t.slice(0, 10)) + ' ' + t.slice(11, 16) + ' IST' +
        T('. Lists may have changed since then — confirm on hcraj.nic.in', '। इसके बाद सूची बदली हो सकती है — hcraj.nic.in पर पुष्टि करें');
    }
    window.__stale = msg;
    if (el && msg) { el.hidden = false; el.textContent = msg; }
    if (window.__onStale) window.__onStale();
  }
  window.__off = function (s) { if (s && (!offAt || s < offAt)) { offAt = s; paintStale(); } };
  window.addEventListener('online', function () {
    var el = $('stale'), b;
    if (!offAt || !el || el.querySelector('button')) return;
    b = D.createElement('button'); b.type = 'button'; b.className = 'lk';
    b.textContent = T('Back online — reload for the latest lists', 'इंटरनेट वापस आ गया — ताज़ा सूची के लिए रीलोड करें');
    b.onclick = function () { location.reload(); };
    el.appendChild(b);
  });
  function swStart(warm) {           // installable + instant repeat visits; never on file:// or the absolute-root 404 page
    if (!('serviceWorker' in navigator) || /^https?:/.test(ROOT) || !/^https?:$/.test(location.protocol)) return;
    var first = !navigator.serviceWorker.controller;
    navigator.serviceWorker.register(ROOT + 'sw.js').then(function () { return navigator.serviceWorker.ready; }).then(function (reg) {
      if (first && warm && reg.active) reg.active.postMessage({ warm: warm });
    }).catch(function () { /* the site works without it */ });
  }
  function getJSON(path, ver, fresh) {
    var url = ROOT + path + (ver ? '?v=' + ver : ''), tries = 0;
    function once() {
      var ctl = window.AbortController ? new AbortController() : null;
      var tm = setTimeout(function () { if (ctl) ctl.abort(); }, 25000);
      return fetch(url, { signal: ctl ? ctl.signal : undefined, cache: fresh ? 'no-cache' : 'default' }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        window.__off(+r.headers.get('x-sw-saved'));
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
    var msg = '', now = istNow();
    if (!st || !st.written_at) msg = 'Data may be out of date — the update status could not be loaded; confirm on hcraj.nic.in';
    else {
      var age = (Date.now() - Date.parse(st.written_at)) / 3600000;
      if ((age > 26 && now.getUTCDay() !== 0) || age > 50 || (st.next_hearing_day && todayIso() > st.next_hearing_day))
        msg = 'Data may be out of date — last updated ' + nice(st.written_at.slice(0, 10)) + ' ' + st.written_at.slice(11, 16) + ' IST; confirm on hcraj.nic.in';
    }
    staleMsg = msg;
    var lab = D.querySelectorAll('[data-day]'), i, t = todayIso();
    for (i = 0; i < lab.length; i++) if (lab[i].getAttribute('data-day') === t) lab[i].textContent = ' (today)';
    paintStale();
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
  if (BOOT.off) window.__off(BOOT.off);
  (BOOT.status || getJSON('status.json', '', true)).then(function (st) { BOOT.statusVal = st; staleCheck(st); renderUpdated(st); }, function () { staleCheck(null); });
  if (body.getAttribute('data-page') !== 'home') { window.addEventListener('load', function () { swStart(); }); return; }

  // ---------------------------------------------------------------- home
  var meta = null, days = {}, shards = {}, runId = 0, shown = 50, liveT = 0, fuzzT = 0, keyAt = 0;
  var form = $('f'), qIn = $('q'), whenSel = $('when'), dateIn = $('date'), seatSel = $('seat'), out = $('res'), state = $('state'), countEl = $('count');
  var PAGE = 50;
  var PH0 = out.innerHTML, cnShards = {}, cnrCtx = null;      // PH0 = the picture guide shown before a search
  var boardOn = false, backY = null, lastHash = location.hash;
  function hp() {                    // #q=..&b=..&h=..&d=..&seat=..  ->  {q, b, h, d, seat}
    var o = {};
    location.hash.replace(/^#/, '').split('&').forEach(function (kv) {
      var i = kv.indexOf('=');
      if (i > 0) { try { o[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' ')); } catch (e) { /* a malformed link must not break the page */ } }
    });
    return o;
  }
  function setUrl(hash, push) {
    try { history[push ? 'pushState' : 'replaceState'](push ? { b: 1 } : null, '', hash || (location.pathname + location.search)); } catch (e) { /* ignore */ }
    lastHash = location.hash;
  }
  function toRes() { window.scrollTo(0, Math.max(0, out.getBoundingClientRect().top + window.pageYOffset - 8)); }     // bring the answer into view
  function restoreY() { if (backY != null) { window.scrollTo(0, backY); backY = null; } }
  function tyName(typ) { var v = meta && meta.ct && meta.ct[typ]; return v ? ((HI && v[1]) || v[0]) : ''; }   // full form of a case type, only where we are sure
  function bKey(iso, seat, n) { return iso + '.' + seat + '.' + encodeURIComponent(n); }
  function legend() {
    return '<p class="lg">' + T('D = Daily list · S = Supplementary list · D+S = in both · “with item” = connected with that item and taken up along with it · tap a court’s name for its full board',
      'D = डेली लिस्ट · S = सप्लीमेंट्री लिस्ट · D+S = दोनों में · “with item” = उस आइटम से जुड़ा मामला, उसी के साथ लिया जाता है · पूरा बोर्ड देखने के लिए कोर्ट के नाम पर टैप करें') + '</p>';
  }

  // ---- "When is my case listed?": the answer card. Built ONLY from lists we hold (the all-dates index):
  //      a coming listing is shown as it stands in a published list; nothing is forecast, and the court's own
  //      next date (case status) is never claimed.
  function heldUpTo(seat) {          // 'Jodhpur 25-Nov-2026 · Jaipur 23-Nov-2026': the latest date of which we hold a list
    return SEATS.filter(function (s) { return !seat || s === seat; }).map(function (s) {
      var mx = '';
      Object.keys(meta.lists || {}).forEach(function (d) { var l = meta.lists[d][s]; if (d > mx && l && ((l.D && l.D[0] === 'list') || (l.S && l.S[0] === 'list'))) mx = d; });
      return mx ? SN[s] + ' ' + nice(mx) : '';
    }).filter(Boolean).join(' · ');
  }
  function wc(hh, seat, saved) {     // the answer card for one case, with this page's language, dates and board links
    return whenCard(hh, { today: todayIso(), hi: HI, held: heldUpTo(seat), saved: saved, part: function (x) {     // from the day file of that date, when it is in memory
      var day = days[x.date] && days[x.date]._ready, e = day && (day._case[x.num + '/' + x.year] || []).filter(function (e) { return e.seat === x.seat && e.typ === x.key && e.c.n === x.court && e.r[0] === x.item; })[0];
      return e ? partLabel(e.c, e.r[6]) : '';
    }, link: function (x) { return meta.days[x.date] ? '#b=' + bKey(x.date, x.seat, x.court) + '&h=' + encodeURIComponent(x.typ + ' ' + x.num + '/' + x.year) : ''; } });
  }
  function whenCards(all, seat, q) {  // one card per distinct case (seat + type) behind a number/year; all = index hits, newest first
    var by = {}, order = [], h = '';
    all.forEach(function (x) { var k = x.seat + '|' + x.key + '|' + x.num + '|' + x.year; if (!by[k]) { by[k] = []; order.push(k); } by[k].push(x); });
    order.slice(0, 4).forEach(function (k) {
      var x = by[k][0], lab = x.typ + ' ' + x.num + '/' + x.year;
      h += '<div class="when"><div class="wt">' + esc(lab) + ' · ' + SFULL[x.seat] + (tyName(x.key) ? ' · ' + esc(tyName(x.key)) : '') + '</div>' + wc(by[k], x.seat) + '<div class="wa">' + svBtn('c', k, lab) + '</div></div>';
    });
    if (!order.length) h = '<div class="when"><div class="wt">' + esc((q.typ ? q.typ + ' ' : '') + q.num + '/' + q.year) + (seat ? ' · ' + SFULL[seat] : '') + '</div>' + wc([], seat) + '</div>';
    return h;
  }

  // ---- My list: saved cases and names. Kept in THIS browser only (localStorage); nothing is sent anywhere. ----
  var MYK = 'rhc_my_v1', MYMAX = { c: 40, a: 15 }, myBox = $('my'), myOpen = false, myRun = 0;
  function myLoad() { try { var j = JSON.parse(localStorage.getItem(MYK) || '{}') || {}; return { c: j.c || [], a: j.a || [] }; } catch (e) { return { c: [], a: [] }; } }
  function myStore() { try { localStorage.setItem(MYK, JSON.stringify(MY)); } catch (e) { /* private mode: kept until the page closes */ } }
  var MY = myLoad();
  function myHas(kind, k) { return MY[kind].some(function (x) { return x.k === k; }); }
  function svText(on) { return on ? T('★ Saved', '★ सेव है') : T('☆ Save', '☆ सेव करें'); }
  function svBtn(kind, k, label, text) {
    var on = myHas(kind, k);
    return '<button class="sv' + (on ? ' on' : '') + '" type="button" data-sv="' + kind + '" data-k="' + esc(k) + '" data-l="' + esc(label) + '"' + (text ? ' data-t="' + esc(text) + '"' : '') +
      ' aria-pressed="' + on + '">' + (text && !on ? '☆ ' + esc(text) : svText(on)) + '</button>';
  }
  function syncSv() {
    var bs = D.querySelectorAll('button.sv'), i, b, on;
    for (i = 0; i < bs.length; i++) {
      b = bs[i]; on = myHas(b.getAttribute('data-sv'), b.getAttribute('data-k'));
      b.className = 'sv' + (on ? ' on' : ''); b.setAttribute('aria-pressed', on);
      b.textContent = b.getAttribute('data-t') && !on ? '☆ ' + b.getAttribute('data-t') : svText(on);
    }
  }
  function dayLabel(iso) { return dmy(iso) + (iso === todayIso() ? T(' (today)', ' (आज)') : ''); }
  function myMiss(iso, day) {
    if (!day) return '<div class="md w">' + dayLabel(iso) + ': ' + T('this list could not be loaded just now', 'यह सूची अभी लोड नहीं हो सकी') + '</div>';
    return '<div class="md w">' + dayLabel(iso) + ': ' + (heldSummary(meta.lists[iso] || day.lists, todayIso())[1] ? T('not found in the lists we hold', 'हमारे पास उपलब्ध सूचियों में नहीं मिला') :
      T('not found so far — we do not hold every list yet', 'अभी तक नहीं मिला — सभी सूचियाँ अभी हमारे पास नहीं हैं')) + '</div>';
  }
  function myCase(x, dd) {           // where a saved case stands on today's and the next hearing day's lists
    var p = x.k.split('|'), h = '', hit = 0;
    dd.forEach(function (iso) {
      var day = days[iso] && days[iso]._ready, hits = day ? (day._case[p[2] + '/' + p[3]] || []).filter(function (e) { return e.seat === p[0] && e.typ === p[1]; }) : [];
      if (!hits.length) { h += myMiss(iso, day); return; }
      hit = 1;
      h += '<div class="md"><b>' + dayLabel(iso) + '</b>: ' + hits.map(function (e) {
        return '<a href="#b=' + esc(bKey(iso, e.seat, e.c.n)) + '&h=' + encodeURIComponent(x.l) + '">' + esc(SN[e.seat] + ' ' + courtName(e.c.n) + ', ' + (/^with/i.test(e.r[0]) ? e.r[0].replace(/^with\s*/i, T('with item ', 'आइटम के साथ: ')) : T('Item ', 'आइटम ') + e.r[0])) + '</a>' + badge(e.r[6]) + (e.c.p ? '<span class="bd pa">Part ' + e.c.p + '</span>' : '');
      }).join(' · ') + '</div>';
    });
    return { h: h, hit: hit };
  }
  function myName(x, dd) {           // a saved advocate / party name: court-wise items for the same two days
    var h = '', hit = 0;
    dd.forEach(function (iso) {
      var day = days[iso] && days[iso]._ready, hits = day ? searchDay(day, { kind: 'name', norm: x.k }, '', false).hits : [], g = [], last = null;
      if (!hits.length) { h += myMiss(iso, day); return; }
      hit = 1;
      hits.forEach(function (e) { if (!last || last.c !== e.c) { last = { c: e.c, seat: e.seat, it: [] }; g.push(last); } last.it.push(e.r[0]); });
      h += '<div class="md"><b>' + dayLabel(iso) + '</b>: ' + hits.length + T(hits.length > 1 ? ' matters' : ' matter', hits.length > 1 ? ' मामले' : ' मामला') + ' — ' + g.slice(0, 6).map(function (c) {
        return '<a href="#b=' + esc(bKey(iso, c.seat, c.c.n)) + '&h=' + encodeURIComponent(x.l) + '">' + esc(SN[c.seat] + ' ' + courtName(c.c.n)) + '</a> ' + T('item ', 'आइटम ') + esc(c.it.slice(0, 8).join(', ')) + (c.it.length > 8 ? '…' : '');
      }).join(' · ') + (g.length > 6 ? ' · ' + T('and ' + (g.length - 6) + ' more — tap the name', 'और ' + (g.length - 6) + ' — नाम पर टैप करें') : '') + '</div>';
    });
    return { h: h, hit: hit };
  }
  function myDraw(items, run) {
    var lim = myOpen ? items.length : 6, h = '<h2>★ ' + T('My list', 'मेरी सूची') + ' <small>' + T('saved on this device only', 'केवल इसी डिवाइस में सेव') + '</small></h2><ul class="ml">';
    items.slice(0, lim).forEach(function (it) {
      var x = it.x, p = x.k.split('|'), c = it.kind === 'c';
      h += '<li' + (it.hit ? ' class="hit"' : '') + '><div class="mh"><button class="lk mq" type="button" data-mq="' + esc(x.l) + '"' + (c ? ' data-ms="' + esc(p[0]) + '"' : '') + '>' + esc(x.l) + '</button>' +
        '<span class="ty">' + esc(c ? [tyName(p[1]), SN[p[0]]].filter(Boolean).join(' · ') : T('name search', 'नाम से खोज')) + '</span>' +
        '<button class="mx" type="button" data-sv="' + it.kind + '" data-k="' + esc(x.k) + '" data-l="' + esc(x.l) + '" aria-label="' + T('Remove', 'हटाएँ') + ' ' + esc(x.l) + '">×</button></div>' +
        (it.h || '<div class="md w">' + T('Checking today’s lists…', 'आज की सूची देखी जा रही है…') + '</div>') + (c ? '<div class="mi" data-mi="' + esc(x.k) + '"></div>' : '') + '</li>';
    });
    h += '</ul>' + (items.length > lim ? '<button class="lk" type="button" id="myall">' + T('Show all', 'सभी दिखाएँ') + ' (' + items.length + ')</button>' : '') +
      '<p class="hint">' + T('Today and the next hearing day, from the lists we hold. A case that does not appear here may still be on the court’s board — ' + CONFIRM + '.',
        'आज और अगली सुनवाई की तारीख, हमारे पास उपलब्ध सूचियों से। जो केस यहाँ नहीं दिखता वह भी कोर्ट के बोर्ड पर हो सकता है — hcraj.nic.in पर पुष्टि अवश्य करें।') +
      ' <button class="lk" type="button" id="myclr">' + T('Clear my list', 'मेरी सूची मिटाएँ') + '</button></p>';
    myBox.innerHTML = h; myBox.hidden = false;
    if (meta && run) myDates(items.slice(0, lim).filter(function (it) { return it.kind === 'c'; }), run);
  }
  function myDates(items, run) {     // last / next date of each saved case from the all-dates index, one at a time, after everything else
    var i = 0, today = todayIso();
    (function next() {
      if (run !== myRun || i >= items.length) { if (run === myRun) window.__myDatesDone = run; return; }
      var x = items[i++].x, p = x.k.split('|');
      lookupIndex({ num: +p[2], year: +p[3], typ: p[1] }).then(function (hh) {
        if (run !== myRun) return;
        var els = myBox.querySelectorAll('[data-mi]'), j, old, lt;
        hh = hh.filter(function (r) { return r.seat === p[0] && r.key === p[1]; });          // newest first
        lt = hh.filter(function (r) { return r.date < today; })[0];
        for (j = 0; j < els.length; j++) if (els[j].getAttribute('data-mi') === x.k) {
          old = els[j].parentNode.querySelectorAll('.md');
          while (old.length && old[0].parentNode) { old[0].parentNode.removeChild(old[0]); old = els[j].parentNode.querySelectorAll('.md'); }
          els[j].className = 'mi when';
          els[j].innerHTML = wc(hh, p[0], 1) + '<span class="wr">' + (lt && hh[0].date >= today ? T('Last listed before today: ', 'आज से पहले पिछली बार: ') + nice(lt.date) + ' · ' : '') +
            (hh.length ? '<button class="lk mq" type="button" data-mq="' + esc(x.l) + '" data-ms="' + esc(p[0]) + '" data-mw="all">' + T('all ' + hh.length + ' dates we hold', 'हमारे पास सभी ' + hh.length + ' तारीखें') + '</button>' : '') + '</span>';
        }
      }, function () { /* the index did not load: the line stays empty */ }).then(next);
    })();
  }
  function myRefresh() {             // computed in short slices so typing and the first results never wait for it
    if (!myBox) return;
    var run = ++myRun, dd = meta ? defaultDates() : [], i = 0;
    var items = MY.c.map(function (x) { return { x: x, kind: 'c', hit: 0 }; }).concat(MY.a.map(function (x) { return { x: x, kind: 'a', hit: 0 }; }));
    if (!items.length) { myBox.hidden = true; myBox.innerHTML = ''; return; }
    if (!meta) { myDraw(items, 0); return; }
    (function step() {
      if (run !== myRun) return;
      var t0 = performance.now(), it, r;
      while (i < items.length && performance.now() - t0 < 10) { it = items[i++]; r = it.kind === 'c' ? myCase(it.x, dd) : myName(it.x, dd); it.h = r.h; it.hit = r.hit; }
      if (i < items.length) { setTimeout(step, 0); return; }
      items.sort(function (a, b) { return b.hit - a.hit; });
      myDraw(items, run);
      window.__myListMs = Math.round(performance.now());
    })();
  }
  D.addEventListener('click', function (ev) {
    var b = ev.target, kind = b && b.getAttribute && b.getAttribute('data-sv'), k, had, a;
    if (kind) {
      k = b.getAttribute('data-k'); had = myHas(kind, k);
      if (had) MY[kind] = MY[kind].filter(function (x) { return x.k !== k; });
      else if (MY[kind].length >= MYMAX[kind]) { b.textContent = T('My list is full (' + MYMAX[kind] + ')', 'मेरी सूची भर गई है (' + MYMAX[kind] + ')'); return; }
      else MY[kind].unshift({ k: k, l: b.getAttribute('data-l') });
      myStore(); syncSv(); myRefresh();
      return;
    }
    if (!b || !b.getAttribute) return;
    if (b.id === 'myall') { myOpen = true; myRefresh(); return; }
    if (b.id === 'myclr') {
      if (b.getAttribute('data-sure')) { MY = { c: [], a: [] }; myStore(); syncSv(); myRefresh(); }
      else { b.setAttribute('data-sure', '1'); b.textContent = T('Tap again to remove all saved items', 'सभी सेव आइटम हटाने के लिए फिर टैप करें'); }
      return;
    }
    if (b.getAttribute('data-mq') && meta) {       // a saved item -> run its search
      qIn.value = b.getAttribute('data-mq'); seatSel.value = b.getAttribute('data-ms') || ''; whenSel.value = b.getAttribute('data-mw') || 'def'; dateIn.hidden = true;
      run(false); toRes();
      return;
    }
    if (b.getAttribute('data-pdf')) { a = /^(\d{4}-\d\d-\d\d)\.(jdp|jp)(?:\.(.+))?$/.exec(b.getAttribute('data-pdf')); if (a) makePdf(a[1], a[2], a[3] == null ? null : decodeURIComponent(a[3]), b); return; }
    a = b.closest ? b.closest('a[href^="#b="],a[href^="#l="]') : null;
    if (a) { ev.preventDefault(); backY = window.pageYOffset; setUrl(a.getAttribute('href'), true); route(); }
  });
  myRefresh();                       // the saved names appear at once; where they stand is filled in when the lists are in memory

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
  function badge(lt) { return '<span class="bd ' + ['D', 'S', 'DS'][lt] + '" title="' + (HI ? ['डेली लिस्ट', 'सप्लीमेंट्री लिस्ट', 'डेली और सप्लीमेंट्री दोनों लिस्ट में'] : ['Daily list', 'Supplementary list', 'Daily and Supplementary lists'])[lt] + '">' + ['D', 'S', 'D+S'][lt] + '</span>'; }
  function partLabel(c, lt) {       // "DAILY PART-A" ... from the court's own heading; '' when the list states no part
    var t = lt === 1 ? 'S' : (c.t || 'D');
    return c.p ? (t === 'S' ? 'SUPPLEMENTARY' : 'DAILY') + ' PART-' + c.p : '';
  }
  // mine = undefined for search results (court names open the board); an array on the board itself (those rows are marked)
  function renderHits(hits, budget, iso, mine, bare) {
    var h = '', lastSeat = null, lastC = null, open = false, n = 0, withT = T('Connected with this item; taken up along with it', 'इस आइटम से जुड़ा मामला; उसी के साथ लिया जाता है');
    hits.forEach(function (e) {
      if (n >= budget) return;
      n++;
      if (e.seat !== lastSeat) { if (open) { h += '</div>'; open = false; } if (!bare) h += '<h3 class="seat">' + SFULL[e.seat] + '</h3>'; lastSeat = e.seat; lastC = null; }
      if (e.c !== lastC) {
        if (open) h += '</div>';
        var pl = partLabel(e.c, e.r[6]);
        h += '<div class="court"><div class="ch"' + (mine ? '' : ' role="button" tabindex="0" data-b="' + esc(bKey(iso, e.seat, e.c.n)) + '"') + '><b>' + esc(courtName(e.c.n)) + '</b>' + (pl ? ' <span class="pt-l">' + pl + '</span>' : '') + (e.c.s ? ' · ' + esc(e.c.s) : '') +
          (mine ? (pl ? '' : ' · ' + (e.c.r.every(function (x) { return x[6] === 1; }) ? TN.S : TN.D) + ' list') : '<em>' + T('Full board ›', 'पूरा बोर्ड ›') + '</em>') + '<span>' + esc(e.c.j) + '</span></div>';
        open = true; lastC = e.c;
      }
      var r = e.r, wm = /^with\s+(.*)$/i.exec(r[0]), ty = tyName(e.typ);
      h += '<div class="it' + (mine && mine.indexOf(e) >= 0 ? ' me' : '') + '"><div class="no">' + (wm ? '<small title="' + withT + '">with item</small>' + esc(wm[1]) : '<small>item</small>' + esc(r[0])) + '</div>' +
        '<div class="cn">' + esc(r[1]) + badge(r[6]) + (e.c.p ? '<span class="bd pa">Part ' + e.c.p + '</span>' : '') + (ty ? '<span class="ty">' + esc(ty) + '</span>' : '') + '</div>' +
        (r[2] === 0 ? '<div class="pt wh">(party names withheld)</div>' : '<div class="pt">' + esc(r[2]) + ' <i>vs</i> ' + esc(r[3]) + '</div>') +
        '<div class="co">Petitioner: ' + esc(r[4] || '-') + ' · Respondent: ' + esc(r[5] || '-') + '</div>' +
        (bare ? '</div>' : '<div class="act"><button class="sh" type="button" data-sh="' + esc(r[1] + ' — ' + SN[e.seat] + ' ' + courtName(e.c.n).replace('Court No.', 'Court') + ', Item ' + r[0] + ' (' + (partLabel(e.c, r[6]) || ['D', 'S', 'D+S'][r[6]]) + ') on ' + dmy(iso || '') + '.') + '" data-c="' + esc(r[1]) + '">Share</button>' +
        (e.num != null ? svBtn('c', e.seat + '|' + e.typ + '|' + e.num + '|' + e.year, r[1]) : '') + '</div></div>');
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
  function idxPart(x) {
    var day = days[x.date] && days[x.date]._ready, e = day && (day._case[x.num + '/' + x.year] || []).filter(function (e) { return e.seat === x.seat && e.typ === x.key && e.c.n === x.court && e.r[0] === x.item; })[0];
    return e && e.c.p ? '<span class="bd pa">Part ' + e.c.p + '</span>' : '';
  }
  function indexTable(hits, skip) {
    var rows = hits.filter(function (x) { return !skip[x.date]; });
    if (!rows.length) return '';
    var h = '<div class="card"><h2>' + esc(rows[0].num + '/' + rows[0].year) + ': ' + (Object.keys(skip).length ? 'other dates' : 'dates') + ' in our database (' + rows.length + ')</h2><div class="tw"><table><tr><th>Date</th><th>Seat</th><th>Court</th><th>Item</th><th>Type</th><th></th></tr>';
    rows.slice(0, 400).forEach(function (x) {
      h += '<tr><td class="n">' + dmy(x.date) + '</td><td>' + SN[x.seat] + '</td><td>' + esc(x.court) + '</td><td>' + esc(x.item) + '</td><td>' + esc(x.typ) + badge(x.lt) + idxPart(x) + '</td><td>' +
        (meta.days[x.date] ? '<button class="lk" type="button" data-d="' + x.date + '">details</button>' : '') + '</td></tr>';
    });
    var seen = {}, sv = '';
    rows.forEach(function (x) {
      var k = x.seat + '|' + x.key + '|' + x.num + '|' + x.year, lab = x.typ + ' ' + x.num + '/' + x.year;
      if (seen[k] || Object.keys(seen).length >= 4) return;
      seen[k] = 1;
      sv += svBtn('c', k, lab, T('Save ', 'सेव करें: ') + lab + ' (' + SN[x.seat] + ')' + (tyName(x.key) ? ' — ' + tyName(x.key) : '')) + ' ';
    });
    return h + '</table></div><p class="svn">' + sv + '</p><p class="hint"><b>Actual listings only — tentative / next dates are not shown.</b> <span lang="hi">केवल वास्तविक लिस्टिंग; संभावित (टेंटेटिव) तारीखें नहीं।</span> Court and item from our database. Parties and counsel are shown for the last ' + meta.site_days + ' days (“details”); older full lists are in our database but not on this site. A date missing here may still be on the court’s board — ' + CONFIRM + '.</p><p class="unoff">Unofficial compilation — not a website of the Rajasthan High Court; the court’s published cause list is the only official record.</p></div>';
  }

  // live = typed (no fuzzy pass, no history entry); otherwise an explicit search
  function run(live, keep) {
    var raw = qIn.value.trim(), my = ++runId;
    window.__runId = my;
    clearTimeout(fuzzT);
    boardOn = false;
    if (!keep) backY = null;
    if (!meta) { show('', 'Loading today’s list…'); pending = true; return; }
    if (!raw) { show(PH0); window.__searchDone = my; restoreY(); return; }
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
    var dates = sc.dates, parts = {}, done = 0, idx = null, idxErr = false, fuzzy = false, t0 = performance.now(), ms = 0, card = null;   // card: null = asking, 0 = failed, [] = answer
    if (!keep) shown = PAGE;
    if (!live) setUrl('#q=' + encodeURIComponent(raw));

    function paint(final) {
      if (my !== runId) return;
      var h = '', total = 0, budget = shown, nf = [], loose = false, skip = {};
      if (cnrHead) h += cnrHead;
      if (q.kind === 'case' && card !== 0) h += card ? whenCards(card, seat, q) : '<div class="when wait"><div class="wt">' + T('When is it listed? Checking every list we hold…', 'कब लगा है? हमारे पास उपलब्ध सभी सूचियाँ देखी जा रही हैं…') + '</div></div>';
      if (note) h += '<div class="warn">' + esc(note) + '</div>';
      if (final && q.kind === 'name' && !cm) h += '<p class="svn">' + svBtn('a', q.norm, raw.toUpperCase(), T('Save “' + raw + '” to My list', '“' + raw + '” को मेरी सूची में सेव करें')) + '</p>';
      if (dates.some(function (iso) { return parts[iso] && parts[iso].hits.length; })) h += legend();
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
      restoreY();
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
    if (q.kind === 'case') idxP = lookupIndex(q).then(function (hh) { card = seat ? hh.filter(function (x) { return x.seat === seat; }) : hh; if (wantIdx) idx = card; }, function () { if (wantIdx) idxErr = true; card = 0; });
    var c = pass(false);
    function afterExact() {
      if (my !== runId) return;
      if (q.kind === 'name' && !any()) {
        if (live) { finish(); fuzzT = setTimeout(function () { if (my === runId) fuzzyPass(); }, 350); return; }
        return fuzzyPass();
      }
      finish();
    }
    if (!c) { afterExact(); if (idxP) idxP.then(function () { if (my === runId) { paint(true); window.__cardDone = my; } }); return; }      // everything was in memory: answered in this tick
    show('', 'Searching…');
    Promise.all([c, idxP]).then(function () { afterExact(); window.__cardDone = my; });
  }


  // ---- Download PDF: a real .pdf written in the browser - no library, no server, nothing stored in the repo.
  //      A4, the standard Helvetica of every PDF reader (so no font is embedded), firm header, page numbers.
  //      Built in short slices: a 2,500-row list never freezes a phone.
  var HW = '278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 278 278 584 584 584 556 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 556 556 333 500 278 556 500 722 500 500 500 334 260 334 584'.split(' ');
  var MONF = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'], DOWF = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  function longDate(iso) { return DOWF[new Date(iso + 'T00:00:00Z').getUTCDay()] + ' ' + parseInt(iso.slice(8, 10), 10) + ' ' + MONF[parseInt(iso.slice(5, 7), 10) - 1] + ' ' + iso.slice(0, 4); }
  function pclean(s) { return String(s == null ? '' : s).replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[–—·]/g, '-').replace(/\s+/g, ' ').replace(/[^\x20-\x7e]/g, '?'); }
  function pw(s, size) { var w = 0, i, c; for (i = 0; i < s.length; i++) { c = s.charCodeAt(i); w += c >= 32 && c < 127 ? +HW[c - 32] : 556; } return w * size / 1000; }
  function pwrap(s, size, width) {
    var lines = [], cur = '';
    pclean(s).split(' ').forEach(function (w) {
      var k, t;
      while (pw(w, size) > width) { k = w.length; while (k > 1 && pw(w.slice(0, k), size) > width) k--; if (cur) { lines.push(cur); cur = ''; } lines.push(w.slice(0, k)); w = w.slice(k); }
      t = cur ? cur + ' ' + w : w;
      if (cur && pw(t, size) > width) { lines.push(cur); cur = w; } else cur = t;
    });
    if (cur || !lines.length) lines.push(cur);
    return lines;
  }
  function ptxt(x, y, s, size, font, rgb) { return 'BT /' + (font || 'F1') + ' ' + size + ' Tf ' + (rgb || '.04 .1 .2') + ' rg ' + x.toFixed(1) + ' ' + y.toFixed(1) + ' Td (' + pclean(s).replace(/[\\()]/g, '\\$&') + ') Tj ET\n'; }
  function pcirc(x, y, r) { var k = r * 0.5523; return [x + r, y, 'm', x + r, y + k, x + k, y + r, x, y + r, 'c', x - k, y + r, x - r, y + k, x - r, y, 'c', x - r, y - k, x - k, y - r, x, y - r, 'c', x + k, y - r, x + r, y - k, x + r, y, 'c'].map(function (v) { return typeof v === 'number' ? v.toFixed(1) : v; }).join(' ') + ' '; }
  function makePdf(iso, seat, court, btn) {
    if (!meta || !meta.days[iso]) return;
    var label = btn ? btn.textContent : '';
    function say(t) { if (btn) btn.textContent = t; }
    say(T('Preparing the PDF…', 'PDF तैयार हो रही है…'));
    loadDay(iso).then(function (day) {
      var rows = day._rows.filter(function (e) { return e.seat === seat && (court == null || e.c.n === court); }), t0 = performance.now();
      var W = 595, H = 842, M = 34, X = [M, 84, 206, 408], CW = [44, 116, 196, W - M - 408], pages = [], cur = '', y = 0, i = 0, lastC = null;
      var ls = (meta.lists[iso] || day.lists)[seat] || {}, kinds = [ls.D && ls.D[0] === 'list' ? 'Daily' : '', ls.S && ls.S[0] === 'list' ? 'Supplementary' : ''].filter(Boolean).join(' + ');
      var title = 'Rajasthan High Court, ' + (seat === 'jdp' ? 'Jodhpur' : 'Jaipur Bench') + ' - Cause List - ' + longDate(iso) + (kinds ? ' - ' + kinds : '') + (court != null ? ' - ' + courtName(court) : '');
      var stamp = istNow().toISOString().slice(0, 16).replace('T', ' ') + ' IST';
      if (!rows.length) { say(label); return; }
      function newPage() { if (cur) pages.push(cur); cur = ''; y = H - M - 46; }
      function head(c, contd) {
        var bench = pwrap(c.j || '', 8, W - 2 * M - 12), hgt = 15 + bench.length * 9.5;
        cur += '.94 .91 .84 rg ' + M + ' ' + (y - hgt + 9).toFixed(1) + ' ' + (W - 2 * M) + ' ' + hgt.toFixed(1) + ' re f\n' +
          ptxt(M + 6, y - 2, courtName(c.n) + (c.s ? ' - ' + c.s : '') + ' - ' + (partLabel(c, c.r.length && c.r.every(function (r) { return r[6] === 1; }) ? 1 : 0) || (c.r.every(function (r) { return r[6] === 1; }) ? 'Supplementary list' : 'Daily list')) + (contd ? ' (continued)' : ''), 9.5, 'F2');
        bench.forEach(function (b, k) { cur += ptxt(M + 6, y - 12.5 - k * 9.5, b, 8); });
        y -= hgt + 5;
      }
      newPage();
      (function step() {
        var t1 = performance.now(), e, r, cols, n, k, h;
        while (i < rows.length && performance.now() - t1 < 14) {
          e = rows[i++]; r = e.r;
          cols = [[r[0]], pwrap(r[1] + '  [' + ['D', 'S', 'D+S'][r[6]] + (e.c.p ? ' Part ' + e.c.p : '') + ']', 8, CW[1]), r[2] === 0 ? ['(party names withheld)'] : pwrap(r[2] + ' vs ' + r[3], 8, CW[2]),
            pwrap('P: ' + (r[4] || '-'), 7.5, CW[3]).concat(pwrap('R: ' + (r[5] || '-'), 7.5, CW[3]))];
          n = Math.max(cols[1].length, cols[2].length, cols[3].length); h = n * 8.9 + 4;
          if (e.c !== lastC) {                               // a court heading never stays alone at the foot of a page
            if (y - (34 + pwrap(e.c.j || '', 8, W - 2 * M - 12).length * 9.5 + h) < M + 20) newPage();
            head(e.c, false); lastC = e.c;
          } else if (y - h < M + 20) { newPage(); head(e.c, true); }
          cur += ptxt(X[0] + 2, y, cols[0][0], 8.5, 'F2');
          for (k = 0; k < n; k++) cur += (cols[1][k] ? ptxt(X[1], y - k * 8.9, cols[1][k], 8, k ? 'F1' : 'F2') : '') + (cols[2][k] ? ptxt(X[2], y - k * 8.9, cols[2][k], 8) : '') + (cols[3][k] ? ptxt(X[3], y - k * 8.9, cols[3][k], 7.5, 'F1', '.24 .29 .39') : '');
          y -= h;
          cur += '.9 .87 .8 RG .4 w ' + M + ' ' + (y + 6).toFixed(1) + ' m ' + (W - M) + ' ' + (y + 6).toFixed(1) + ' l S\n';
        }
        if (i < rows.length) { say(T('Preparing the PDF… ', 'PDF तैयार हो रही है… ') + Math.round(100 * i / rows.length) + '%'); setTimeout(step, 0); return; }
        pages.push(cur);
        var out = '%PDF-1.4\n', offs = [], N = pages.length, tl = pwrap(title, 10, W - 2 * M - 34);
        function obj(s) { offs.push(out.length); out += offs.length + ' 0 obj\n' + s + '\nendobj\n'; }
        obj('<< /Type /Catalog /Pages 2 0 R >>');
        obj('<< /Type /Pages /Count ' + N + ' /Kids [' + pages.map(function (_, k) { return (6 + 2 * k) + ' 0 R'; }).join(' ') + '] >>');
        ['Helvetica', 'Helvetica-Bold', 'Times-Roman'].forEach(function (f) { obj('<< /Type /Font /Subtype /Type1 /BaseFont /' + f + ' /Encoding /WinAnsiEncoding >>'); });
        pages.forEach(function (body, k) {
          var top = H - M, c = '.043 .102 .2 rg ' + pcirc(M + 13, top - 13, 13) + 'f\n.79 .64 .3 RG .8 w ' + pcirc(M + 13, top - 13, 11) + 'S\n' + ptxt(M + 4.2, top - 15.2, 'SO&A', 6.4, 'F3', '.79 .64 .3');
          tl.forEach(function (t, j) { c += ptxt(M + 32, top - 8 - j * 11, t, 10, 'F2'); });
          c += ptxt(M + 32, top - 8 - tl.length * 11 + 1.5, 'Compiled by Shubham Ojha & Associates, Advocates, Jodhpur - from the cause lists published by the court - UNOFFICIAL: always confirm on hcraj.nic.in', 6.6, 'F1', '.24 .29 .39') +
            '.79 .64 .3 RG 1.2 w ' + M + ' ' + (top - 34) + ' m ' + (W - M) + ' ' + (top - 34) + ' l S\n' + body +
            '.79 .64 .3 RG .6 w ' + M + ' ' + (M + 8) + ' m ' + (W - M) + ' ' + (M + 8) + ' l S\n' +
            ptxt(M, M - 1, 'Actual listings only - tentative / next dates are not shown. Generated ' + stamp + ' - shubhamojhaandassociates.com/rhc-causelist', 6.4, 'F1', '.24 .29 .39') +
            ptxt(W - M - 50, M - 1, 'Page ' + (k + 1) + ' of ' + N, 7.5, 'F2');
          obj('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ' + W + ' ' + H + '] /Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> /Contents ' + (offs.length + 2) + ' 0 R >>');
          obj('<< /Length ' + c.length + ' >>\nstream\n' + c + 'endstream');
        });
        var xref = out.length, a;
        out += 'xref\n0 ' + (offs.length + 1) + '\n0000000000 65535 f \n' + offs.map(function (o) { return ('000000000' + o).slice(-10) + ' 00000 n \n'; }).join('') +
          'trailer\n<< /Size ' + (offs.length + 1) + ' /Root 1 0 R >>\nstartxref\n' + xref + '\n%%EOF\n';
        a = D.createElement('a');
        a.href = URL.createObjectURL(new Blob([out], { type: 'application/pdf' }));
        a.download = 'RHC-' + (seat === 'jdp' ? 'Jodhpur' : 'Jaipur') + '-cause-list-' + iso + (court != null ? '-court-' + String(court).replace(/[^A-Za-z0-9]+/g, '-') : '') + '.pdf';
        D.body.appendChild(a); a.click();
        setTimeout(function () { URL.revokeObjectURL(a.href); D.body.removeChild(a); }, 60000);
        window.__pdf = { ms: Math.round(performance.now() - t0), pages: N, rows: rows.length, bytes: out.length, name: a.download };
        say(T('PDF ready — ' + N + ' pages. Download again', 'PDF तैयार — ' + N + ' पेज। फिर डाउनलोड करें'));
      })();
    }, function () { say(label); });
  }
  function viewBar(iso, seat, court) {     // the buttons above a board / a full list
    var slug = seat === 'jdp' ? 'jodhpur' : 'jaipur';
    return '<p class="vb"><button class="go pdf" type="button" data-pdf="' + esc(iso + '.' + seat + (court != null ? '.' + encodeURIComponent(court) : '')) + '">' + T('⬇ Download PDF', '⬇ PDF डाउनलोड करें') + '</button>' +
      (court != null ? '<a href="#l=' + iso + '.' + seat + '">' + T('View full list — ', 'पूरी सूची देखें — ') + SN[seat] + '</a>' : '') +
      '<a href="' + ROOT + slug + '/' + iso + '/">' + T('Court-wise summary', 'कोर्ट-वार सारांश') + '</a></p>';
  }
  // ---- full list of one seat for one date, court by court; appended in slices so a phone stays responsive ----
  function listView(lk, auto) {
    var m = /^(\d{4}-\d\d-\d\d)\.(jdp|jp)$/.exec(lk || ''), my = ++runId, today = todayIso(), t0 = performance.now();
    var back = '<p class="bk"><button class="lk" type="button" id="back">' + T('← Back to the search', '← खोज पर वापस') + '</button></p>';
    window.__runId = my; clearTimeout(fuzzT); boardOn = true; printAll = null;
    function fail(t) { show(back + '<div class="held nf">' + t + '</div>', ''); window.__searchDone = my; }
    if (!m) { fail('This link is not complete — ' + CONFIRM); return; }
    var iso = m[1], seat = m[2];
    if (!meta.days[iso]) {
      fail(meta.dates.indexOf(iso) >= 0 ? '<b>The full list for ' + dmy(iso) + ' is not shown on this site</b>It is in our database, but full lists here cover the last ' + meta.site_days + ' days. Search by case number with “All dates” to see the court and item — ' + CONFIRM + HI_CONFIRM :
        '<b>We do not hold a list for ' + dmy(iso) + '</b>' + CONFIRM.charAt(0).toUpperCase() + CONFIRM.slice(1) + HI_CONFIRM);
      return;
    }
    show('', T('Loading the list…', 'सूची लोड हो रही है…'));
    loadDay(iso).then(function (day) {
      if (my !== runId) return;
      var rows = day._rows.filter(function (e) { return e.seat === seat; }), lists = meta.lists[iso] || day.lists, pos = 0, title = SFULL[seat] + ' · ' + wnice(iso);
      if (!rows.length) { fail('<b>We do not hold a list of ' + SN[seat] + ' for ' + dmy(iso) + '</b>' + esc(heldSummary(lists, today)[0]) + ' — ' + CONFIRM + HI_CONFIRM); return; }
      show(back + '<h2 class="date">' + T('Full list', 'पूरी सूची') + ' · ' + esc(title) + (iso === today ? ' · today' : '') + ' · ' + rows.length + ' items</h2>' + viewBar(iso, seat, null) +
        (auto ? '<div class="held mine">' + T('Your PDF is being prepared and will download by itself. If it does not, tap “Download PDF”.', 'आपकी PDF तैयार हो रही है और अपने-आप डाउनलोड होगी। न हो तो “PDF डाउनलोड करें” दबाएँ।') + '</div>' : '') +
        legend().replace(/ · [^·]*<\/p>$/, '</p>') + '<div class="held">Lists we hold for ' + dmy(iso) + ': ' + esc(heldSummary(lists, today)[0]) + ' — ' + CONFIRM + '</div><div id="fl"></div><p class="hint" id="flp"></p>',
        rows.length + ' items', 'Full list: ' + title);
      toRes();
      if (auto) makePdf(iso, seat, null, out.querySelector('[data-pdf]'));
      var box = $('fl');
      (function step() {
        if (my !== runId || !box.parentNode) return;
        var end = pos, n = 0;
        while (end < rows.length && (n < 120 || rows[end].c === rows[end - 1].c)) { end++; n++; }
        box.insertAdjacentHTML('beforeend', renderHits(rows.slice(pos, end), 1e9, iso, [], true));
        pos = end;
        if (pos < rows.length) { $('flp').textContent = pos + ' / ' + rows.length + '…'; setTimeout(step, 0); return; }
        $('flp').textContent = ''; $('tools').hidden = false;
        window.__listMs = Math.round(performance.now() - t0);
        window.__searchDone = my;
      })();
    }, function () { if (my === runId) fail('Published data is not available to us right now — ' + CONFIRM); });
  }
  // ---- court board: every item of one court for one date, in the order printed (names as in the day file: redaction applies) ----
  function board(bk, hl, auto) {
    var m = /^(\d{4}-\d\d-\d\d)\.(jdp|jp)\.(.+)$/.exec(bk || ''), my = ++runId, today = todayIso();
    var back = '<p class="bk"><button class="lk" type="button" id="back">' + T('← Back to results', '← परिणाम पर वापस') + '</button></p>';
    window.__runId = my; clearTimeout(fuzzT); boardOn = true; printAll = null;
    function fail(t) { show(back + '<div class="held nf">' + t + '</div>', ''); window.__searchDone = my; }
    if (!m) { fail('This board link is not complete — ' + CONFIRM); return; }
    var iso = m[1], seat = m[2], n = m[3];
    if (!meta.days[iso]) {
      fail(meta.dates.indexOf(iso) >= 0 ? '<b>The full list for ' + dmy(iso) + ' is not shown on this site</b>It is in our database, but full lists here cover the last ' + meta.site_days + ' days. Search by case number with “All dates” to see the court and item — ' + CONFIRM + HI_CONFIRM :
        '<b>We do not hold a list for ' + dmy(iso) + '</b>' + CONFIRM.charAt(0).toUpperCase() + CONFIRM.slice(1) + HI_CONFIRM);
      return;
    }
    show('', T('Loading the board…', 'बोर्ड लोड हो रहा है…'));
    loadDay(iso).then(function (day) {
      if (my !== runId) return;
      var hits = day._rows.filter(function (e) { return e.seat === seat && e.c.n === n; }), mine = [], q, lists = meta.lists[iso] || day.lists;
      if (!hits.length) { fail('<b>' + esc(courtName(n)) + ' (' + SN[seat] + ') is not in the lists we hold for ' + dmy(iso) + '</b>' + esc(heldSummary(lists, today)[0]) + ' — ' + CONFIRM + HI_CONFIRM); return; }
      if (hl && !CNR_Q.test(hl)) {
        q = detect(hl);
        if (q.kind === 'case' || (!/\d/.test(hl) && q.norm.replace(/ /g, '').length >= 3)) mine = searchDay(day, q, seat, false).hits.filter(function (e) { return e.c.n === n; });
      }
      var title = courtName(n) + ' · ' + SFULL[seat] + ' · ' + dmy(iso), order = [], seenC = {}, ix, nav;
      day.seats[seat].courts.forEach(function (c) { if (!seenC[c.n]) { seenC[c.n] = 1; order.push(c.n); } });
      ix = order.indexOf(n);
      nav = '<p class="bnav">' + (ix > 0 ? '<a href="#b=' + esc(bKey(iso, seat, order[ix - 1])) + '">‹ ' + esc(courtName(order[ix - 1])) + '</a>' : '<span></span>') +
        (ix < order.length - 1 ? '<a href="#b=' + esc(bKey(iso, seat, order[ix + 1])) + '">' + esc(courtName(order[ix + 1])) + ' ›</a>' : '') + '</p>';
      show(back + '<h2 class="date">' + T('Full board', 'पूरा बोर्ड') + ' · ' + esc(title) + (iso === today ? ' · today' : '') + ' · ' + hits.length + ' item' + (hits.length > 1 ? 's' : '') + '</h2>' +
        (mine.length ? '<div class="held mine">“' + esc(hl) + '”: ' + mine.length + T(' on this board, marked below — ', ' इस बोर्ड पर, नीचे चिह्नित — ') + esc(mine.slice(0, 12).map(function (e) { return (/^with/i.test(e.r[0]) ? '' : 'item ') + e.r[0]; }).join(', ')) + (mine.length > 12 ? '…' : '') + '</div>' : '') +
        viewBar(iso, seat, n) + nav + legend().replace(/ · [^·]*<\/p>$/, '</p>') +
        '<p class="hint">' + T('Items in the order printed in the court’s lists. The court may take up matters in a different order, and the board can change during the day.', 'आइटम उसी क्रम में हैं जिस क्रम में कोर्ट की सूची में छपे हैं। कोर्ट क्रम बदल सकती है और बोर्ड दिन में बदल सकता है।') + '</p>' +
        renderHits(hits, 1e9, iso, mine) + '<div class="held">Lists we hold for ' + dmy(iso) + ': ' + esc(heldSummary(lists, today)[0]) + ' — ' + CONFIRM + '</div>' + nav + back,
        hits.length + ' item' + (hits.length > 1 ? 's' : '') + ' on this board', 'Full board: ' + title);
      if (auto) makePdf(iso, seat, n, out.querySelector('[data-pdf]'));
      var f = out.querySelector('.it.me');
      if (f) f.scrollIntoView({ block: 'center' }); else toRes();
      window.__searchDone = my;
    }, function () { if (my === runId) fail('Published data is not available to us right now — ' + CONFIRM); });
  }
  function route() {                 // the address decides the view: #b= a court board, otherwise the search
    var p = hp(), was = boardOn;
    if (p.b) { board(p.b, p.h || p.q || '', p.p === '1'); return; }
    if (p.l) { listView(p.l, p.p === '1'); return; }
    boardOn = false;
    if (p.q !== undefined) qIn.value = p.q;
    if (was || p.q !== undefined) run(false, true);
  }
  function onNav() { if (location.hash === lastHash || !meta) return; lastHash = location.hash; route(); }
  window.addEventListener('popstate', onNav);
  window.addEventListener('hashchange', onNav);
  function openBoard(el) { backY = window.pageYOffset; setUrl('#b=' + el.getAttribute('data-b') + (qIn.value.trim() ? '&h=' + encodeURIComponent(qIn.value.trim()) : ''), true); route(); }
  out.addEventListener('keydown', function (ev) {
    var c = ev.target && ev.target.getAttribute && ev.target.getAttribute('data-b');
    if (c && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); openBoard(ev.target); }
  });
  out.addEventListener('click', function (ev) {
    var t = ev.target, d = t && t.getAttribute && t.getAttribute('data-d'), c = t && t.closest && t.closest('[data-b]'), p;
    if (d) { whenSel.value = 'date'; dateIn.hidden = false; dateIn.value = d; run(false); window.scrollTo(0, form.offsetTop); }
    else if (t && t.id === 'back') {
      if (history.state && history.state.b) { history.back(); return; }
      p = hp(); if (!qIn.value.trim() && (p.h || p.q)) qIn.value = p.h || p.q;       // opened from a link: there is no earlier page to go back to
      boardOn = false; setUrl(''); run(false);
    }
    else if (c) openBoard(c);
  });
  whenSel.addEventListener('change', function () { dateIn.hidden = whenSel.value !== 'date'; if (qIn.value.trim()) run(false); });
  dateIn.addEventListener('change', function () { if (qIn.value.trim()) run(false); });
  seatSel.addEventListener('change', function () { if (qIn.value.trim()) run(false); });
  form.addEventListener('submit', function (ev) { ev.preventDefault(); clearTimeout(liveT); keyAt = performance.now(); run(false); if (meta && qIn.value.trim() && out.getBoundingClientRect().top > window.innerHeight * 0.55) toRes(); });
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
    var p0 = hp();
    try { var hq = /[?&]q=([^&]*)/.exec(location.search); if (p0.q === undefined && hq) p0.q = decodeURIComponent(hq[1].replace(/\+/g, ' ')); } catch (e) { /* a malformed link must not break the page */ }
    if (p0.q && !qIn.value) qIn.value = p0.q;
    if (p0.d && meta.days[p0.d]) { whenSel.value = 'date'; dateIn.hidden = false; dateIn.value = p0.d; }     // link from a date page: that date's list
    if (p0.seat === 'jdp' || p0.seat === 'jp') seatSel.value = p0.seat;
    function after() {               // only once the first answer is on screen: My list, then the offline copy
      setTimeout(myRefresh, 0);
      setTimeout(function () {
        swStart([location.href.split('#')[0], 'status.json', 'data/meta.json'].concat(dd.map(function (d) { return 'data/d/cl_' + d + '.json?v=' + meta.days[d]; })));
      }, 1500);
    }
    var loaded = false;
    function label() {                        // never green while we cannot vouch for freshness
      loaded = true;
      if (window.__stale === undefined) return;            // wait for the status answer
      if (window.__stale) { state.className = 'state'; state.textContent = 'Lists loaded (' + dd.map(dmy).join(' and ') + '), but they may be out of date — confirm on hcraj.nic.in.'; return; }
      state.className = 'state ok';
      state.textContent = 'Ready — lists loaded: ' + dd.map(dmy).join(' and ') + '. Results appear as you type.';
    }
    window.__onStale = function () { if (loaded) label(); };
    if (!left) { label(); after(); }
    dd.forEach(function (d) {      // today + next hearing day: fetched in parallel at page open, indexed once
      loadDay(d).then(function (day) {
        indexDay(day, function () {
          days[d]._ready = day;
          window.__warm = (window.__warm || 0) + 1;
          if (--left === 0) { window.__dataReady = performance.now(); label(); if (!boardOn && qIn.value.trim()) run(!pending); pending = false; after(); }
        });
      }, function () {
        if (--left === 0) { window.__dataReady = performance.now(); if (!boardOn && qIn.value.trim()) run(false); after(); }
        state.className = 'state'; state.textContent = 'A list could not be loaded just now. Search still works for what we could load — ' + CONFIRM + '.';
      });
    });
    if (p0.b) board(p0.b, p0.h || p0.q || '', p0.p === '1');
    else if (p0.l) listView(p0.l, p0.p === '1');
    else if (qIn.value.trim()) { pending = true; run(false); }
  }, function () {
    state.textContent = '';
    show('<div class="held nf">Published data is not available to us right now — ' + CONFIRM + '</div>');
    window.__ready = performance.now();
  });
})();
