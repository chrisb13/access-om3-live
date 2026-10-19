// Config Diff page: two experiment dropdowns, the parameters that differ, and, once both are chosen,
// each experiment's settings history (a collapsed section). Data: assets/config_diff/ (see scripts/build_site.py).
(function () {
  var REPO = "https://github.com/ACCESS-Community-Hub/access-om3-experiments", MOM6 = "https://github.com/mom-ocean/MOM6/search?q=";
  var cache = {}, copyCount = 0;
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]; }); };
  function get(url, json) {
    if (!cache[url]) cache[url] = fetch(url).then(function (r) { if (!r.ok) throw new Error(url); return json ? r.json() : r.text(); });
    return cache[url];
  }
  function flat(cfg) { var o = {}; Object.keys(cfg).forEach(function (m) { Object.keys(cfg[m]).forEach(function (v) { o[v] = Object.assign({module: m}, cfg[m][v]); }); }); return o; }
  var norm = function (v) {  // ignore quotes, case and number formatting (1.0E-04 and 0.0001 are the same)
    if (v == null) return null; var s = String(v).trim().replace(/^["']|["']$/g, "").toLowerCase(), n = parseFloat(s.replace("d", "e"));
    return isFinite(n) && /^[-+.\d]/.test(s) ? n : s; };
  function diff(A, B) {
    var a = flat(A), b = flat(B), names = Object.keys(Object.assign({}, a, b));
    return names.map(function (v) { var pa = a[v], pb = b[v];
      return {v: v, module: (pa || pb).module, units: (pa && pa.units) || (pb && pb.units) || "", dflt: (pa && pa.default) || (pb && pb.default) || null, a: pa, b: pb, differs: norm(pa && pa.value) !== norm(pb && pb.value)}; })
      .sort(function (x, y) { return x.module.localeCompare(y.module) || x.v.localeCompare(y.v); });
  }
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    var t = document.createElement("textarea"); t.value = text; t.style.position = "fixed"; t.style.opacity = "0"; document.body.appendChild(t); t.select();
    try { document.execCommand("copy"); } catch (e) {} document.body.removeChild(t); return Promise.resolve();
  }
  function enhance(root) {  // Material only adds copy buttons when the page loads, so add them to what we insert
    var dialog = document.querySelector(".md-dialog"), inner = document.querySelector(".md-dialog__inner");
    Array.prototype.forEach.call(root.querySelectorAll("pre > code"), function (code) {
      var pre = code.parentNode; if (pre.querySelector(".md-clipboard")) return; pre.id = "__mid_code_" + (copyCount++);
      var b = document.createElement("button"); b.className = "md-clipboard md-icon"; b.title = "Copy to clipboard"; b.setAttribute("data-clipboard-target", "#" + pre.id + " > code"); pre.insertBefore(b, code);
      b.addEventListener("click", function () { copyText(code.textContent.replace(/\s+$/, "")).then(function () {
        if (!dialog) return; inner.textContent = "Copied to clipboard"; dialog.classList.add("md-dialog--active"); setTimeout(function () { dialog.classList.remove("md-dialog--active"); }, 2000); }); });
    });
  }
  function labelise(root) {  // the column names, for the card layout on a phone
    Array.prototype.forEach.call(root.querySelectorAll("table"), function (t) {
      var hs = Array.prototype.map.call(t.querySelectorAll("thead th"), function (th) { return th.textContent.trim(); });
      Array.prototype.forEach.call(t.querySelectorAll("tbody tr"), function (tr) { Array.prototype.forEach.call(tr.children, function (td, i) { if (hs[i]) td.setAttribute("data-label", hs[i]); }); });
    });
  }
  function start(root, idx, base) {
    var $ = function (id) { return root.querySelector("#" + id); };
    var groups = idx.map(function (e) { return e.group; }).filter(function (g, i, a) { return a.indexOf(g) === i; });
    var options = '<option value="">Choose an experiment…</option>' + groups.map(function (g) {
      return '<optgroup label="' + esc(g) + '">' + idx.map(function (e, i) { return e.group === g ? '<option value="' + i + '">' + esc(e.label) + "</option>" : ""; }).join("") + "</optgroup>"; }).join("");
    root.innerHTML = '<div id="mid"><div class="pair"><div class="field a"><label class="l" for="mda">Experiment A</label><select id="mda">' + options + "</select></div>" +
      '<div><button id="mdswap" type="button" aria-label="Swap A and B">⇄ Swap A and B</button></div>' +
      '<div class="field b"><label class="l" for="mdb">Experiment B</label><select id="mdb">' + options + "</select></div></div>" +
      '<div class="row2"><div class="field"><label class="l" for="mdf">Filter</label><input type="text" id="mdf" placeholder="setting, module or value"></div>' +
      '<label class="chk"><input type="checkbox" id="mdall"> Show unchanged parameters too</label><button id="mdcopy" type="button">Copy as Markdown</button></div></div>' +
      '<div class="refs" id="mdrefs"></div><div class="count" id="mdcount"></div><div id="mdtable"></div>' +
      '<div class="admonition info" id="hint"><p id="hinttext"></p></div>' +
      '<details id="hist" class="note" hidden><summary>Settings changed during these runs</summary><div id="hist-body"></div></details>';
    var ea = null, eb = null, rows = [], stamp = 0;
    function visible() { var q = $("mdf").value.trim().toLowerCase();
      return rows.filter(function (r) { return ($("mdall").checked || r.differs) && (!q || [r.module, r.v, r.a && r.a.value, r.b && r.b.value].join(" ").toLowerCase().indexOf(q) >= 0); }); }
    function cell(r, p, e, cls, label) {
      if (!p) return '<td class="val ' + cls + ' unset" data-label="' + label + '">(default' + (r.dflt ? ": " + esc(r.dflt) : "") + ")</td>";
      return '<td class="val ' + cls + '" data-label="' + label + '"><a href="' + REPO + "/blob/" + e.sha + "/" + (p.file || "MOM_input") + "#L" + p.line + '" target="_blank" rel="noopener">' + esc(p.value) + "</a></td>"; }
    function drawTable() {
      var vis = visible(), mod = "", h = "";
      vis.forEach(function (r) {
        if (r.module !== mod) { mod = r.module; h += '<tr class="mod"><td colspan="4"><a href="' + MOM6 + encodeURIComponent(mod) + '" target="_blank" rel="noopener">' + esc(mod) + "</a></td></tr>"; }
        h += '<tr class="param"><td><a href="' + MOM6 + encodeURIComponent(r.v) + '" target="_blank" rel="noopener">' + esc(r.v) + '</a></td><td class="units">' + esc(r.units) + "</td>" + cell(r, r.a, ea, "a", "A") + cell(r, r.b, eb, "b", "B") + "</tr>"; });
      $("mdtable").innerHTML = vis.length ? '<table class="diff"><thead><tr><th>Setting</th><th>Units</th><th>A</th><th>B</th></tr></thead><tbody>' + h + "</tbody></table>" : "<p>No differences.</p>";
      $("mdrefs").innerHTML = [["A", ea], ["B", eb]].map(function (x) { return '<span class="chip ' + x[0].toLowerCase() + '">' + x[0] + "</span>" + esc(x[1].label) + " @ <code>" + x[1].sha.slice(0, 10) + "</code>"; }).join("<br>");
      $("mdcount").textContent = rows.filter(function (r) { return r.differs; }).length + " of " + rows.length + " parameters differ; " + vis.length + " shown";
    }
    function drawHist(htmlA, htmlB) {
      var both = ea && eb; $("hist").hidden = !both; $("hint").hidden = !!both; if (!both) return;
      $("hist-body").innerHTML = [["A", ea, htmlA], ["B", eb, htmlB]].map(function (x) {  // the two sides must not share row ids
        return '<h3><span class="chip ' + x[0].toLowerCase() + '">' + x[0] + "</span>" + esc(x[1].label) + "</h3>" + x[2].replace(/(id="|href="#)chg-/g, "$1" + x[0] + "-chg-"); }).join("");
      labelise($("hist-body")); enhance($("hist-body"));
    }
    function update() {
      ea = idx[$("mda").value] || null; eb = idx[$("mdb").value] || null;
      history.replaceState(null, "", ea || eb ? "#a=" + (ea ? encodeURIComponent(ea.slug) : "") + "&b=" + (eb ? encodeURIComponent(eb.slug) : "") : location.pathname);
      if (!ea || !eb) {
        $("mdtable").innerHTML = ""; $("mdrefs").textContent = ""; $("mdcount").textContent = "";
        $("hinttext").textContent = !ea && !eb ? "Choose two experiments to compare. You will see which MOM6 parameters differ and, below that, how each experiment's settings changed during its runs."
          : "Now choose experiment " + (ea ? "B" : "A") + ".";
        drawHist(); return;
      }
      var mine = ++stamp; $("mdcount").textContent = "Loading…";
      Promise.all([get(base + ea.slug + ".json", true), get(base + eb.slug + ".json", true), get(base + ea.slug + ".html"), get(base + eb.slug + ".html")]).then(function (r) {
        if (mine !== stamp) return;  // a newer choice has been made meanwhile
        rows = diff(r[0], r[1]); drawTable(); drawHist(r[2], r[3]);
      }).catch(function () { if (mine === stamp) $("mdcount").textContent = "The data for these experiments could not be loaded."; });
    }
    var h = new URLSearchParams(location.hash.slice(1)), find = function (s) { return idx.findIndex(function (e) { return e.slug === s; }); };
    if (find(h.get("a")) >= 0) $("mda").value = find(h.get("a"));
    if (find(h.get("b")) >= 0) $("mdb").value = find(h.get("b"));
    $("mda").onchange = $("mdb").onchange = update;
    $("mdall").onchange = $("mdf").oninput = function () { if (ea && eb && rows.length) drawTable(); };
    $("mdswap").onclick = function () { var t = $("mda").value; $("mda").value = $("mdb").value; $("mdb").value = t; update(); };
    $("mdcopy").onclick = function () {
      if (!ea || !eb || !rows.length) return; var v = function (p) { return p ? p.value : "_(default)_"; };
      var md = ["| Module | Setting | " + ea.label + " | " + eb.label + " |", "|---|---|---|---|"].concat(visible().map(function (r) { return "| " + r.module + " | `" + r.v + "` | " + v(r.a) + " | " + v(r.b) + " |"; })).join("\n");
      copyText(md).then(function () { $("mdcopy").textContent = "Copied!"; setTimeout(function () { $("mdcopy").textContent = "Copy as Markdown"; }, 1500); });
    };
    root.addEventListener("click", function (e) {  // a dot jumps to its row without changing the address, which holds the two choices
      var a = e.target.closest && e.target.closest('a[href^="#A-chg-"], a[href^="#B-chg-"]'); if (!a) return;
      var row = document.getElementById(a.getAttribute("href").slice(1)); if (!row) return;
      e.preventDefault(); Array.prototype.forEach.call(root.querySelectorAll("tr.hit"), function (r) { r.classList.remove("hit"); }); row.classList.add("hit");
      if (row.scrollIntoView) row.scrollIntoView({block: "center", behavior: "smooth"});
    });
    update();
  }
  function init() {
    var root = document.getElementById("config-diff-app"); if (!root || root.dataset.ready) return; root.dataset.ready = "1";
    var base = root.dataset.base;
    get(base + "index.json", true).then(function (idx) {
      if (!idx.length) root.textContent = "No experiment data was available when this page was built."; else start(root, idx, base);
    }).catch(function () { root.textContent = "The experiment data could not be loaded."; });
  }
  typeof document$ !== "undefined" ? document$.subscribe(init) : document.addEventListener("DOMContentLoaded", init);
})();
