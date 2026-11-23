// Figure explorer: overlay line-plot figures from runs on the Experiments page.
// Data comes from scripts/figure_explorer_data.py; plotly.js loads only on this page.
(() => {
  const PLOTLY = "https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.32.0/plotly.min.js";
  const DASH = ["solid", "dash", "dot", "dashdot", "longdash"];
  const DASHARRAY = { solid: "", dash: "6 4", dot: "1.6 3", dashdot: "6 3 1.6 3", longdash: "11 4" };
  // Each run keeps one colour everywhere (its colour_slot, pinned in access-om3-paper-1's RUN_COLOURS): ColorBrewer
  // Set3's hues without its grey (kept for ACCESS-OM2 and references), stepped to read as lines on each background,
  // in two shades (slots 11-21 are the second). "Distinct colours" instead colours the current selection with a
  // palette that passes colour-blind checks (ColorBrewer Dark2 hues), in the order runs were picked.
  const FIXED_LIGHT = ["#02a292", "#7f4281", "#6f9a1f", "#7666b5", "#9e8e05", "#167cb3", "#d96255", "#4b853e", "#c8649d", "#646102", "#a16512",
    "#0d766a", "#672c6a", "#4e7100", "#52408c", "#73680c", "#0a557d", "#ac392f", "#255f16", "#9d3d76", "#4d4a05", "#704508"];
  const FIXED_DARK = ["#02a292", "#9e5fa0", "#648909", "#6a5aa8", "#766a02", "#0c70a3", "#b03c33", "#407932", "#c06c9b", "#93912c", "#c07b20",
    "#4dccba", "#c886c9", "#8ab243", "#9081d2", "#9d913b", "#4298cd", "#dd6559", "#66a158", "#ec94c4", "#bbba59", "#eba350"];
  const DISTINCT_LIGHT = ["#1f78b4", "#d95f02", "#1b9e77", "#7570b3", "#a6761d", "#e7298a", "#4f8f16"];
  const DISTINCT_DARK = ["#3f93dc", "#df6f22", "#19ab83", "#8c7fe6", "#c18030", "#e64b98", "#5fa92b"];
  // ACCESS-OM2 reference runs: selectable like runs, in fixed neutral colours
  const OM2_NAME = { IAF: "ACCESS-OM2 IAF", RYF: "ACCESS-OM2 RYF", OM2: "ACCESS-OM2" };
  const OM2_LIGHT = { IAF: "#55555e", RYF: "#9b9ba3", OM2: "#77777f" }, OM2_DARK = { IAF: "#d6d6dc", RYF: "#8e8e97", OM2: "#b0b0b8" };
  const om2Of = r => /\bOM2\b|omip2|jra55_(iaf|ryf)|ryf9091/i.test(r.name || "") ? (/ryf/i.test(r.name) ? "RYF" : /iaf/i.test(r.name) ? "IAF" : "OM2") : null;
  const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  // matplotlib mathtext in labels ("10$^6$ km$^2$", "$^\\circ$N", "year$^{-1}$") as <sup>/<sub>; Plotly reads both
  const tex = s => esc(s).replace(/\$\^\\circ\$/g, "°").replace(/\$\^\{?([^${}]+)\}?\$/g, "<sup>$1</sup>")
    .replace(/\$_\{?([^${}]+)\}?\$/g, "<sub>$1</sub>").replace(/\$/g, "").replace(/\n/g, "<br>");
  const links = s => esc(s).replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2">$1</a>');
  const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const json = url => fetch(url).then(r => { if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json(); });
  let plotly = null;
  const loadPlotly = () => plotly ??= window.Plotly ? Promise.resolve() : new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = PLOTLY; s.onload = ok; s.onerror = () => fail(new Error("plotly.js did not load"));
    document.head.append(s);
  });

  const TEMPLATE = `<details class="fx-key"><summary>Experiments, colours and where the data comes from</summary><div class="fx-key-body"></div></details>
    <div class="fx">
    <aside class="fx-rail"><label class="fx-vh" for="fx-q">Search figures</label>
      <input id="fx-q" class="fx-q" type="search" placeholder="Search figures" autocomplete="off" spellcheck="false">
      <p class="fx-count" aria-live="polite"></p><nav class="fx-index"></nav></aside>
    <div class="fx-main"><div class="fx-crumbs"></div><h2 class="fx-title"></h2><p class="fx-sub"></p>
      <div class="fx-runs"></div><div class="fx-controls"></div><div class="fx-legend"></div><p class="fx-missing" aria-live="polite"></p><div class="fx-plots"></div>
      <section class="fx-use" aria-label="Use this figure"></section>
      <details class="fx-details"><summary>Run details</summary><div class="fx-details-body"></div></details>
      <div class="fx-notes"></div></div></div>`;

  function init() {
    const root = document.getElementById("fx-app");
    if (!root) return;
    const base = root.dataset.base;
    root.innerHTML = TEMPLATE;
    Promise.all([json(base + "index.json"), loadPlotly()])
      .then(([M]) => start(root, base, M))
      .catch(e => { root.innerHTML = `<p>Could not load the figure explorer (${esc(e.message)}).</p>`; });
  }

  function start(root, base, M) {
    const $ = c => root.querySelector(c);
    const FIG = M.figures, RUNS = Object.fromEntries(M.runs.map(r => [r.name, r]));
    const BY_ID = Object.fromEntries(M.runs.map(r => [r.id, r.name]));
    const NB = Object.fromEntries(M.categories.flatMap(c => c.notebooks.map(n => [n.nb, { ...n, category: c.name }])));
    const ORDER = M.categories.flatMap(c => c.notebooks.flatMap(n => n.figures));
    const GROUPS = [...new Set(M.runs.map(r => r.group))];
    const state = { fig: null, runs: [], xUser: "auto", raw: true, refs: true, hidden: new Set(), q: "", open: new Set(),
      om2off: new Set(), refoff: new Set(), distinct: false };
    const slot = new Map(), cache = new Map();
    let version = 0;

    if (!ORDER.length) {
      $(".fx-main").innerHTML = "<p>No figure data yet. Runs appear here once their figure data is in access-om3-paper-1's index.</p>";
      $(".fx-count").textContent = M.runs.length ? `${M.runs.length} runs on the Experiments page, none with data yet.` : "No figure data yet.";
      return;
    }

    const load = (fid, name) => { const k = `${fid}|${name}`; if (!cache.has(k)) cache.set(k, json(`${base}data/${fid}/${RUNS[name].id}.json`)); return cache.get(k); };
    const loadRefs = fid => { const k = `${fid}|refs`; if (!cache.has(k)) cache.set(k, json(`${base}data/${fid}/refs.json`)); return cache.get(k); };
    const isTime = f => f.x_kind === "time";
    const forcingOf = name => RUNS[name].forcing;
    const active = f => state.runs.filter(n => f.runs[n]);
    const autoAxis = f => active(f).some(n => forcingOf(n) === "RYF") ? "years" : "date";
    const axis = f => !isTime(f) ? "number" : (state.xUser === "auto" ? autoAxis(f) : state.xUser);
    const theme = () => {
      const cs = getComputedStyle(root), v = n => cs.getPropertyValue(n).trim();
      const bg = getComputedStyle(document.body).backgroundColor.match(/\d+/g)?.map(Number) || [255, 255, 255];
      const dark = (0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]) < 128;
      return { ink: v("--md-default-fg-color") || "#222", muted: v("--md-default-fg-color--light") || "#666",
        rule: v("--md-default-fg-color--lightest") || "#ddd", bg: v("--md-default-bg-color") || "#fff",
        ref: dark ? "#9aa3a9" : "#737b80", refShades: dark ? ["#ededf2", "#b4b4bd", "#82828b"] : ["#26262b", "#696972", "#a0a0a8"], fixed: dark ? FIXED_DARK : FIXED_LIGHT, distinct: dark ? DISTINCT_DARK : DISTINCT_LIGHT,
        om2: dark ? OM2_DARK : OM2_LIGHT };
    };
    const colour = (n, T) => state.distinct ? T.distinct[slot.get(n) % T.distinct.length]
      : T.fixed[(RUNS[n].colour_slot ?? slot.get(n)) % T.fixed.length];
    const yrs = (d, start) => { const [Y, Mo, D] = d.slice(0, 10).split("-").map(Number); return +(Y - start + ((Mo - 1) * 30.44 + D) / 365.25).toFixed(3); };
    const dashOf = (name, names) => name ? DASH[Math.max(0, names.indexOf(name)) % DASH.length] : "solid";
    const sample = (c, dash, w, marker) => marker
      ? `<svg width="26" height="12" aria-hidden="true"><path d="M9 2 L17 10 M17 2 L9 10" stroke="${c}" stroke-width="1.6"/></svg>`
      : `<svg width="26" height="12" aria-hidden="true"><line x1="1" y1="6" x2="25" y2="6" stroke="${c}" stroke-width="${w}" stroke-dasharray="${DASHARRAY[dash] || ""}"/></svg>`;
    const period = r => [r.forcing, r.length].filter(Boolean).join(", ");
    // hover text: complete information about a run or a comparison line, and how to open its data
    const runInfo = n => { const r = RUNS[n];
      return [n, [period(r), r.group].filter(Boolean).join(" · "), r.datastore ? `ESM datastore: ${r.datastore}` : "",
        r.datastore ? `intake.open_esm_datastore("${r.datastore}", columns_with_iterables=["variable"])` : "",
        r.evaluation_url ? `Evaluation: ${r.evaluation_url}` : ""].filter(Boolean).join("\n"); };
    const refInfo = r => [r.name || "Unlabelled in the notebook", r.intake ? `ACCESS-NRI intake catalog: ${r.intake}` : "",
      r.source || "", !r.intake && !r.source ? `Drawn by ${FIG[state.fig]?.notebook}.ipynb; see the notebook for its source` : ""].filter(Boolean).join("\n");

    // ---- search index ----
    const HAY = {};
    for (const id of ORDER) {
      const f = FIG[id], nb = NB[f.notebook];
      HAY[id] = [f.display, f.subtitle, f.heading_sub, f.variable, f.notebook, nb?.category, f.caption, ...f.panels.map(p => `${p.title} ${p.y_label}`)]
        .join(" ").toLowerCase();
    }
    const terms = () => state.q.toLowerCase().split(/\s+/).filter(Boolean);
    const hits = (id, ts) => ts.every(t => new RegExp("(^|[^a-z0-9])" + reEsc(t)).test(HAY[id]));
    const hl = (text, ts) => !ts.length ? esc(text)
      : esc(text).replace(new RegExp("(^|[^a-z0-9])(" + ts.map(t => reEsc(esc(t))).join("|") + ")", "gi"), "$1<mark>$2</mark>");

    function rail() {
      const ts = terms();
      let shown = 0;
      $(".fx-index").innerHTML = M.categories.map(c => {
        let n = 0;
        const nbs = c.notebooks.map(nb => {
          const ids = nb.figures.filter(id => hits(id, ts));
          n += ids.length;
          if (!ids.length) return "";
          return `<div class="fx-nb"><div class="fx-nb-h"><a href="${esc(nb.source)}" title="Notebook source">${hl(nb.nb, ts)}</a><span class="fx-n">${ids.length}</span></div>`
            + (nb.issues ? `<div class="fx-nb-i">${nb.issues}</div>` : "")
            + `<ul>${ids.map(id => { const f = FIG[id], k = Object.keys(f.runs).length;
              return `<li><button type="button" class="fx-fig" data-id="${id}" aria-current="${id === state.fig}"><span class="fx-fl">${hl(f.display, ts)}</span><span class="fx-n" title="Data for ${k} of ${M.runs.length} runs">${k}</span></button></li>`; }).join("")}</ul></div>`;
        }).join("");
        shown += n;
        if (!n) return "";
        const open = ts.length > 0 || state.open.has(c.name);
        return `<div class="fx-cat" data-open="${open ? 1 : 0}"><button type="button" class="fx-cat-h" data-cat="${esc(c.name)}" aria-expanded="${open}"><span class="fx-caret" aria-hidden="true">▸</span><span>${hl(c.name, ts)}</span><span class="fx-n">${n}</span></button><div class="fx-cat-b">${nbs}</div></div>`;
      }).join("") || `<p>No figures match “${esc(state.q)}”. Try a variable such as thetaoga, a place such as Drake, or a notebook name.</p>`;
      $(".fx-count").textContent = ts.length ? `${shown} of ${ORDER.length} figures match` : `${ORDER.length} line-plot figures. Try Drake, thetaoga or AMOC.`;
    }

    // ---- main column ----
    function key() {
      const T = theme(), app = root.dataset;
      const rows = M.runs.map(r => {
        const n = ORDER.filter(id => FIG[id].runs[r.name]).length;
        return `<tr><td>${sample(T.fixed[(r.colour_slot ?? 0) % T.fixed.length], "solid", 3)}</td><th scope="row">${esc(r.name)}</th><td>${esc(r.group || "")}</td>`
          + `<td>${esc(r.forcing)}</td><td>${esc(r.length || "")}</td><td class="fx-num">${n} of ${ORDER.length}</td>`
          + `<td><code>${esc(r.datastore || "")}</code></td><td>${r.evaluation_url ? `<a href="${esc(r.evaluation_url)}">Evaluation</a>` : ""}`
          + `${r.figshare_article ? ` <a href="https://figshare.com/articles/figure/${r.figshare_article}">Figshare</a>` : ""}</td></tr>`; }).join("");
      $(".fx-key-body").innerHTML = `<p>Each experiment keeps its colour on every figure. Its lines are the values its own notebooks
        drew when they made its <a href="${esc(app.evaluation || "")}">evaluation pages</a>, which stay the reference for each run.
        The figure count shows how many of the explorer's figures have data for the run: a figure is missing for a run when its
        notebook did not draw it (for example a notebook that failed, or one that does not apply to RYF runs).</p>
        <div class="fx-scroll"><table><thead><tr><th></th><th>Experiment</th><th>Group</th><th>Forcing</th><th>Length</th><th>Figures</th>
        <th>ESM datastore</th><th>Pages</th></tr></thead><tbody>${rows}</tbody></table></div>
        <p>Comparison lines (ACCESS-OM2, other models, observations) are drawn in greys; hover a legend entry for where its data
        comes from. Found something wrong or confusing? See <a href="${esc(app.contributing || "")}">Contributing and feedback</a>:
        include the figure's link (<b>Copy link</b>) and its <b>Summary</b> from <b>Use this figure</b>.</p>`;
    }
    function header(f) {
      const nb = NB[f.notebook];
      $(".fx-crumbs").innerHTML = `${esc(nb?.category || "")} › <a href="${esc(nb?.source || "#")}">${esc(f.notebook)}.ipynb</a>`;
      $(".fx-title").textContent = f.heading || f.display;  // e.g. the variable, with its long name under it
      $(".fx-sub").textContent = f.heading_sub || f.subtitle;
    }
    function runChips(f) {
      $(".fx-runs").innerHTML = GROUPS.map(g => `<div class="fx-grp"><div class="fx-grp-h">${esc(g)}</div><div class="fx-chips">${M.runs.filter(r => r.group === g).map(r => {
        const on = state.runs.includes(r.name), has = !!f.runs[r.name];
        const tip = has ? `${period(r)}${r.forcing_mismatch ? `. Base Configuration says ${r.base_configuration}` : ""}` : (r.has_data ? "No line data for this figure" : "No figure data for this run yet");
        return `<label class="fx-chip${on ? " on" : ""}${has ? "" : " na"}"${on ? ` style="--fx-chip:${colour(r.name, theme())}"` : ""} title="${esc(tip)}"><input type="checkbox" value="${esc(r.name)}"${on ? " checked" : ""}><span class="fx-sw"></span><span class="fx-nm">${esc(r.name)}</span><span class="fx-badge${r.forcing_mismatch ? " warn" : ""}">${esc(r.forcing)}</span></label>`;
      }).join("")}</div></div>`).join("")
        + `<div class="fx-grp"><div class="fx-grp-h">ACCESS-OM2 (reference)</div><div class="fx-chips fx-om2"></div></div>`
        + `<div class="fx-grp"><div class="fx-grp-h">Observations &amp; other models</div><div class="fx-chips fx-refs"></div></div>`;
    }
    function om2Chips(kinds, T) {
      $(".fx-om2").innerHTML = kinds.length ? kinds.map(k => {
        const on = !state.om2off.has(k);
        return `<label class="fx-chip${on ? " on" : ""}" style="--fx-chip:${T.om2[k]}" title="ACCESS-OM2 lines drawn in this figure's notebook (fixed colour)"><input type="checkbox" data-om2="${k}"${on ? " checked" : ""}><span class="fx-sw"></span><span class="fx-nm">${OM2_NAME[k]}</span></label>`;
      }).join("") : '<span class="fx-k">None in this figure</span>';
    }
    // each observation / other-model line: its own chip and style (dash x shade), the same while others are toggled
    function refChips(refNames, refStyle) {
      $(".fx-refs").innerHTML = refNames.length ? refNames.map(n => {
        const on = !state.refoff.has(n), st = refStyle(n);
        return `<label class="fx-chip${on ? " on" : ""}" style="--fx-chip:currentColor"><input type="checkbox" data-ref="${esc(n)}"${on ? " checked" : ""}>${sample(st.color, st.dash, 1.6)}<span class="fx-nm">${esc(n || "Unlabelled lines")}</span></label>`;
      }).join("") : '<span class="fx-k">None in this figure</span>';
    }
    function controls(f, names) {
      const time = isTime(f), chosen = axis(f);
      const why = state.xUser === "auto" && time ? (chosen === "years" ? "Auto: years since start, because an RYF run is selected" : "Auto: model date, because every selected run is IAF") : "";
      const raw = f.panels && Object.keys(f.runs).length && f._hasThin;
      $(".fx-controls").innerHTML =
        `<div class="fx-seg" role="radiogroup" aria-label="Time axis" aria-disabled="${!time}">` +
        [["auto", "Auto"], ["date", "Model date"], ["years", "Years since start"]].map(([v, t]) =>
          `<label><input type="radio" name="fx-x" value="${v}"${state.xUser === v ? " checked" : ""}${time ? "" : " disabled"}>${t}</label>`).join("") + `</div>` +
        `<label class="fx-ctl" aria-disabled="${!raw}"><input type="checkbox" data-c="raw"${state.raw ? " checked" : ""}${raw ? "" : " disabled"}>Monthly values</label>` +

        `<label class="fx-ctl" title="Colour just the selected runs with a palette that is easier to tell apart, including for colour-blind readers. Off: each run keeps its own colour on every figure."><input type="checkbox" data-c="distinct"${state.distinct ? " checked" : ""}>Distinct colours</label>` +
        (names.length ? `<span class="fx-series">${names.map(n => `<label class="fx-chip${state.hidden.has(n) ? "" : " on"}" style="--fx-chip:currentColor"><input type="checkbox" data-series="${esc(n)}"${state.hidden.has(n) ? "" : " checked"}>${sample("currentColor", dashOf(n, names), 1.7)}<span>${esc(n)}</span></label>`).join("")}</span>` : "") +
        `<button type="button" class="fx-btn" data-c="copy">Copy link</button>` + (why ? `<span class="fx-why">${why}</span>` : "");
    }

    async function plots(f) {
      const my = ++version, T = theme(), host = $(".fx-plots"), on = active(f), mode = axis(f);
      const [datas, refs] = await Promise.all([Promise.all(on.map(n => load(f.id, n))), loadRefs(f.id)]);
      if (my !== version) return;
      const kinds = ["IAF", "RYF", "OM2"].filter(k => refs.some(r => (r.kind === "line" || r.kind === "points") && om2Of(r) === k));
      om2Chips(kinds, T);
      const om2Names = {};  // each OM2 kind's lines, in order: their dash
      for (const r of refs) { const k = (r.kind === "line" || r.kind === "points") && om2Of(r); if (k && !(om2Names[k] ||= []).includes(r.name)) om2Names[k].push(r.name); }
      const om2Dash = (k, name) => DASH[Math.max(0, om2Names[k].indexOf(name)) % DASH.length];
      const isOm2 = r => (r.kind === "line" || r.kind === "points") && om2Of(r);
      const refNames = [...new Set(refs.filter(r => !isOm2(r) && (r.name || r.kind === "line" || r.kind === "points")).map(r => r.name || ""))];
      const refStyle = n => { const i = Math.max(0, refNames.indexOf(n));
        return { color: T.refShades[Math.floor(i / DASH.length) % T.refShades.length], dash: n ? DASH[i % DASH.length] : "dot" }; };
      refChips(refNames, refStyle);
      const perPanel = Math.max(0, ...f.panels.map((_, i) => new Set(datas.flatMap(d => (d.panels[i]?.lines || []).map(l => l.name).filter(Boolean))).size));
      const names = perPanel > 1 ? [...new Set(datas.flatMap(d => d.panels.flatMap(p => p.lines.map(l => l.name).filter(Boolean))))] : [];
      f._hasThin = datas.some(d => d.panels.some(p => p.lines.some(l => l.style === "thin"))) || refs.some(r => r.thin);
      controls(f, names);
      if (!on.length) {
        const have = M.runs.filter(r => f.runs[r.name]).map(r => r.name);
        host.innerHTML = `<div class="fx-empty">${state.runs.length
          ? `<b>${state.runs.map(esc).join(", ")}</b> ${state.runs.length > 1 ? "have" : "has"} no data for this figure: ${esc(f.notebook)}.ipynb did not draw it for ${state.runs.length > 1 ? "these runs" : "this run"} (the notebook failed for it, or does not apply to it).<br>Runs with this figure (solid outline above): ${have.map(esc).join(", ")}.`
          : "Choose one or more runs above."}</div>`;
        delete host.dataset.fig;  // the panes are gone: make them again when a run is picked
        $(".fx-missing").innerHTML = "";
        legend(f, on, [], names, T, om2Dash, null); useBox(f, on, []); notes(f, on); return;
      }
      const missing = state.runs.filter(n => !f.runs[n]);  // picked, but without this figure: say so rather than draw nothing
      $(".fx-missing").innerHTML = missing.length ? `No data for this figure from <b>${missing.map(esc).join(", ")}</b>: ${esc(f.notebook)}.ipynb did not draw it for ${missing.length > 1 ? "these runs" : "this run"}.` : "";
      const n = f.panels.length, wide = host.clientWidth;
      host.style.setProperty("--fx-cols", n === 1 ? 1 : (n === 3 && wide > 1020 ? 3 : (wide > 620 ? 2 : 1)));
      host.style.setProperty("--fx-h", n === 1 ? "420px" : "300px");
      if (host.dataset.fig !== f.id) {
        host.querySelectorAll(".fx-pane").forEach(d => window.Plotly.purge(d));
        host.innerHTML = f.panels.map((p, i) => `<div>${n > 1 ? `<h3>${tex(p.title)}</h3>` : ""}<div class="fx-pane" data-i="${i}"></div></div>`).join("");
        host.dataset.fig = f.id;
      }
      f.panels.forEach((p, i) => {
        const traces = [], rtraces = [], shapes = [], ann = [];  // rtraces: references, drawn under the runs
        on.forEach((name, k) => {
          const c = colour(name, T), start = RUNS[name].start_year;
          for (const s of datas[k].panels[i]?.lines || []) {
            if ((s.name && state.hidden.has(s.name)) || (s.style === "thin" && !state.raw)) continue;
            const thin = s.style === "thin", pts = s.style === "points";
            traces.push({ type: "scatter", mode: pts ? "markers" : "lines", x: mode === "years" ? s.x.map(d => typeof d === "string" ? yrs(d, start ?? +d.slice(0, 4)) : null) : s.x, y: s.y,
              line: { color: c, width: thin ? 0.8 : 1.6, dash: dashOf(s.name, names) }, marker: { color: c, size: 6 }, opacity: thin ? 0.25 : 0.85,
              hoverinfo: thin ? "skip" : undefined, hovertemplate: thin ? undefined : `<b>${esc(name)}</b>${s.name ? " – " + esc(s.name) : ""}<br>%{x}<br>%{y:.4~g}<extra></extra>` });
          }
        });
        let k = 0;
        const x0 = traces.find(t => t.x?.length)?.x[0];
        // start zoomed to the selected runs: a 650-year OM2 reference would otherwise squeeze them (double-click shows all)
        let xr;
        if (isTime(f)) for (const t of traces) for (const v of t.x) if (v != null) xr = xr ? [v < xr[0] ? v : xr[0], v > xr[1] ? v : xr[1]] : [v, v];
        for (const r of refs.filter(r => r.panel === i)) {
          const om2 = (r.kind === "line" || r.kind === "points") && om2Of(r);
          if (om2 ? state.om2off.has(om2) : state.refoff.has(r.name || "")) continue;
          if (r.thin && !state.raw) continue;  // a reference's monthly values, behind its running mean
          if (r.kind === "line" || r.kind === "points") {
            if (isTime(f) && mode === "years" && typeof r.x[0] !== "string") continue;
            const rc = om2 ? T.om2[om2] : refStyle(r.name || "").color;
            rtraces.push({ type: "scatter", mode: r.kind === "points" ? "markers" : "lines", x: mode === "years" ? r.x.map(d => yrs(d, +r.x[0].slice(0, 4))) : r.x, y: r.y,
              line: { color: rc, width: r.thin ? 0.7 : om2 ? 1.1 : 1.4, dash: r.thin ? "solid" : om2 ? om2Dash(om2, r.name) : refStyle(r.name || "").dash },
              opacity: r.thin ? 0.3 : om2 ? 0.7 : 0.9, hoverinfo: r.thin ? "skip" : undefined,
              marker: { color: rc, size: 7, symbol: "x-thin", line: { width: 1.6, color: rc } },
              ...(r.yerr ? { error_y: { type: "data", symmetric: false, color: rc, thickness: 1, width: 3,
                array: r.yerr.map((e, j) => e && r.y[j] != null ? e[1] - r.y[j] : null), arrayminus: r.yerr.map((e, j) => e && r.y[j] != null ? r.y[j] - e[0] : null) } } : {}),
              hovertemplate: `<b>${esc(r.name || "Unlabelled in the notebook")}</b><br>%{x}<br>%{y:.4~g}<extra>${om2 ? "ACCESS-OM2" : "reference"}</extra>` });
          } else if (r.kind === "hline" && !r.name) {  // e.g. a zero line: drawn like an axis line, nothing to label
            shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: r.y, y1: r.y, line: { color: T.muted, width: 0.8 }, layer: "below" });
          } else if (r.kind === "hline") {
            shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: r.y, y1: r.y, line: { color: refStyle(r.name).color, width: 1.3, dash: refStyle(r.name).dash } });
            ann.push({ xref: "paper", x: 0.005, xanchor: "left", y: r.y, yanchor: "bottom", yshift: 2, showarrow: false, bgcolor: T.bg, opacity: 0.92,
              text: esc(r.name) + (mode === "years" ? " (observed-period mean)" : ""), font: { size: 11, color: T.ink } });
            if (x0 !== undefined) traces.push({ type: "scatter", mode: "markers", x: [x0], y: [r.y], marker: { opacity: 0 }, hoverinfo: "skip" });
          } else if (r.kind === "band") {
            shapes.push({ type: "rect", xref: "paper", x0: 0, x1: 1, y0: r.y[0], y1: r.y[1], fillcolor: refStyle(r.name || "").color, opacity: 0.09, line: { width: 0 }, layer: "below" });
          } else if (r.kind === "span" && mode === "date") {
            shapes.push({ type: "rect", xref: "x", yref: "paper", x0: r.x[0], x1: r.x[1], y0: 0, y1: 1, fillcolor: T.ref, opacity: 0.13, line: { width: 0 }, layer: "below" });
          }
        }
        const ax = { gridcolor: T.rule, linecolor: T.rule, zerolinecolor: T.rule, tickfont: { size: 11 } };
        const xTitle = isTime(f) ? (mode === "years" ? "Years since start of run" : "Model date") : (p.x_label || "");
        const months = f.x_kind !== "time" && /month/i.test(p.x_label || "") ? { tickmode: "array", tickvals: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], ticktext: MONTHS } : {};
        window.Plotly.react(host.querySelector(`.fx-pane[data-i="${i}"]`), [...rtraces, ...traces], {
          margin: { l: 66, r: 14, t: 8, b: 42 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", showlegend: false, hovermode: "closest",
          font: { family: getComputedStyle(root).fontFamily, size: 12, color: T.ink }, hoverlabel: { bgcolor: T.bg, bordercolor: T.rule, font: { color: T.ink } },
          xaxis: { ...ax, ...months, ...(xr ? { range: xr } : {}), type: isTime(f) && mode === "date" ? "date" : "linear", title: { text: tex(xTitle), font: { size: 12, color: T.muted } } },
          yaxis: { ...ax, type: p.y_log ? "log" : "linear", exponentformat: "power", automargin: true, title: { text: tex(p.y_label), font: { size: 12, color: T.muted } } },
          shapes, annotations: ann, uirevision: f.id + mode,
        }, { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "toggleSpikelines"] });
      });
      legend(f, on, refs, names, T, om2Dash, refStyle); useBox(f, on, refs); notes(f, on);
    }

    function legend(f, on, refs, names, T, om2Dash, refStyle) {
      const items = on.map(name => {
        const r = RUNS[name], avg = (f.runs[name].periods || []).join("; ");
        return `<span class="fx-it" title="${esc(runInfo(name))}">${sample(colour(name, T), "solid", 2.2)}<span><b>${esc(name)}</b> <span class="fx-k">${esc(period(r))}${avg ? `; averaging ${esc(avg)}` : ""}</span></span></span>`;
      });
      names.forEach(n => items.push(`<span class="fx-it">${sample(T.ink, dashOf(n, names), 1.7)}<span>${esc(n)}</span></span>`));
      if (on.length && state.raw && f._hasThin) items.push('<span class="fx-it fx-k">Thin lines: monthly values. Thick lines: as drawn in the notebook (e.g. a running mean).</span>');
      const seen = new Set();
      for (const r of refs) {  // ACCESS-OM2 lines, in their fixed colours
        const om2 = (r.kind === "line" || r.kind === "points") && om2Of(r);
        if (!om2 || state.om2off.has(om2) || seen.has(r.name)) continue;
        seen.add(r.name);
        items.push(`<span class="fx-it" title="${esc(refInfo(r))}">${sample(T.om2[om2], om2Dash(om2, r.name), 1.5)}<span class="fx-k">${esc(r.name)}</span></span>`);
      }
      for (const r of refs) {  // observations and other models, in the styles their chips show
        if (((r.kind === "line" || r.kind === "points") && om2Of(r)) || !refStyle || state.refoff.has(r.name || "")) continue;
        if (!r.name && r.kind !== "line" && r.kind !== "points") continue;
        const key = `${r.kind}|${r.name}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const st = refStyle(r.name || "");
        const s = r.kind === "points" ? sample(st.color, "", 0, true)
          : r.kind === "band" ? `<svg width="26" height="12" aria-hidden="true"><rect x="1" y="2" width="24" height="8" fill="${st.color}" opacity="0.25"/></svg>`
          : sample(st.color, st.dash, 1.5);
        items.push(`<span class="fx-it" title="${esc(refInfo(r))}">${s}<span class="fx-k">${esc(r.name || "Other lines from the notebook, unlabelled there")}${r.kind === "band" ? " (range)" : ""}</span></span>`);
      }
      $(".fx-legend").innerHTML = items.join("");
    }

    // ---- "Use this figure": a Markdown summary, a short Python script, a PNG of the plot ----
    function mdSummary(f, on, refs) {
      const nb = NB[f.notebook], fignum = Object.values(f.runs)[0]?.fignum ?? "";
      const shown = [...new Set(refs.filter(r => r.name && ((r.kind === "line" || r.kind === "points") && om2Of(r) ? !state.om2off.has(om2Of(r)) : !state.refoff.has(r.name))).map(r => r.name))];
      const rows = on.map(n => { const r = RUNS[n];
        return `| ${n} | ${r.forcing} | ${r.length || ""} | \`${r.datastore}\` | ${r.evaluation_url ? `[evaluation pages](${r.evaluation_url})` : ""} | ${r.figshare_article ? `[Figshare ${r.figshare_article}](https://figshare.com/articles/figure/${r.figshare_article})` : "not published yet"} |`; });
      const prov = RUNS[on[0]]?.provenance || {};
      return [`### ${f.display}${f.subtitle ? ` (${f.subtitle})` : ""}`, "",
        `From [\`${f.notebook}.ipynb\`](${nb?.source || ""}) (figure ${fignum}) of the ACCESS-OM3 paper 1 evaluation notebooks.`,
        `Viewed in the ACCESS-OM3 figure explorer: ${location.href}`, "",
        "| Run | Forcing | Length | ESM datastore | Evaluation | Figure data |", "|---|---|---|---|---|---|", ...rows, "",
        shown.length ? "Comparison data shown:" : "No comparison data shown.",
        ...shown.map(n => { const r = refs.find(x => x.name === n);
          return `- ${n}: ${r.intake ? `ACCESS-NRI intake catalog, \`${r.intake}\`` : r.source || `drawn by ${f.notebook}.ipynb (see the notebook)`}`; }), "",
        prov.paper_commit ? `Line data made by access-om3-paper-1 ${prov.paper_commit.slice(0, 7)} with mkfigs ${String(prov.mkfigs_commit || "").slice(0, 12)} (${(prov.generated_at || "").slice(0, 10)}).` : ""].join("\n").trim();
    }
    function pyScript(f, on) {
      const nb = NB[f.notebook], q = s => JSON.stringify(s);
      const stores = on.map(n => `    ${q(n)}: ${q(RUNS[n].datastore)},`).join("\n");
      const head = `"""${f.display}: the runs selected in the ACCESS-OM3 figure explorer.\n\n`;
      const env = `Run on Gadi (e.g. an ARE JupyterLab session) with the ACCESS-NRI environment:\n    module use /g/data/xp65/public/modules && module load conda/analysis3\nThe full analysis is in ${f.notebook}.ipynb: ${nb?.source || ""}\n"""`;
      const variable = f.notebook === "timeseries" ? (f.variable || f.display.split(":")[0].trim()) : "";
      if (variable) return `${head}Each run's monthly ${variable}, read from its ESM datastore with intake, as ${f.notebook}.ipynb does.\n${env}
import intake
import matplotlib.pyplot as plt

# Each run's intake-esm datastore (from the ACCESS-OM3 Experiments page)
DATASTORES = {
${stores}
}
VARIABLE = ${q(variable)}

fig, ax = plt.subplots(figsize=(10, 4))
for run, path in DATASTORES.items():
    catalog = intake.open_esm_datastore(path, columns_with_iterables=["variable"])
    # the monthly means of this global diagnostic
    ds = catalog.search(variable=VARIABLE, frequency="1mon").to_dask()
    ds[VARIABLE].plot(ax=ax, label=run, linewidth=1)
ax.set_title(${q(f.display)})
ax.legend(fontsize=8)
plt.show()
`;
      const urls = on.filter(n => RUNS[n].data_url).map(n => `    ${q(n)}: ${q(RUNS[n].data_url)},`).join("\n");
      return `${head}Plots the values this page shows: the line data ${f.notebook}.ipynb saved with the figure, from each
run's figure-data file. To recompute from the model output instead, open each run's ESM datastore with
intake (below) and follow the notebook.
${env}
import gzip
import json
import urllib.request

import intake
import matplotlib.pyplot as plt
import pandas as pd

# Each run's intake-esm datastore (from the ACCESS-OM3 Experiments page), and its figure-data file
DATASTORES = {
${stores}
}
FIGURE_DATA = {
${urls}
}
FIGURE = ${q(f.key)}  # this figure's key in the figure-data files

# The model output, for recomputing: e.g. catalogs[run].search(variable="aice_m", frequency="1mon").to_dask()
catalogs = {run: intake.open_esm_datastore(path, columns_with_iterables=["variable"]) for run, path in DATASTORES.items()}

fig, axs = plt.subplots(1, ${f.panels.length}, figsize=(${Math.min(5 * f.panels.length, 20)}, 4), squeeze=False)
for run, url in FIGURE_DATA.items():
    with urllib.request.urlopen(url) as response:
        figures = json.loads(gzip.decompress(response.read()))["figures"]
    record = next(r for r in figures if r["key"] == FIGURE)
    for ax, panel in zip(axs.flat, record["panels"]):
        for line in panel["lines"]:
            if line["role"] == "own":  # this run's lines (the others are references)
                x = pd.to_datetime(line["x"]) if panel["x_kind"] == "time" else line["x"]
                ax.plot(x, line["y"], label=f"{run} {line['label']}".strip(), linewidth=1)
        ax.set_title(panel["title"], fontsize=9)
        ax.set_ylabel(panel["y_label"])
axs.flat[0].legend(fontsize=7)
plt.show()
`;
    }
    // the summary's Markdown (only what mdSummary writes: a heading, paragraphs, a table, a list), rendered
    function mdHtml(md) {
      const inline = t => esc(t).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2">$1</a>');
      const cells = l => l.replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      const out = [], lines = md.split("\n");
      for (let i = 0; i < lines.length; i++) {
        const l = lines[i];
        if (l.startsWith("### ")) out.push(`<h4>${inline(l.slice(4))}</h4>`);
        else if (l.startsWith("|")) {
          const rows = []; while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]); i--;
          const [head, , ...body] = rows;
          out.push(`<div class="fx-scroll"><table><thead><tr>${cells(head).map(c => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>`
            + body.map(r => `<tr>${cells(r).map(c => `<td>${inline(c)}</td>`).join("")}</tr>`).join("") + "</tbody></table></div>");
        } else if (l.startsWith("- ")) {
          const items = []; while (i < lines.length && lines[i].startsWith("- ")) items.push(lines[i++].slice(2)); i--;
          out.push(`<ul>${items.map(t => `<li>${inline(t)}</li>`).join("")}</ul>`);
        } else if (l.trim()) out.push(`<p>${inline(l)}</p>`);
      }
      return out.join("");
    }
    function useBox(f, on, refs) {
      const box = $(".fx-use");
      if (!on.length) { box.innerHTML = ""; return; }
      box.innerHTML = `<h3>Use this figure</h3>
        <div class="fx-use-row"><button type="button" class="fx-btn" data-u="png" data-label="Export PNG">Export PNG</button>
          <button type="button" class="fx-btn" data-u="md" data-label="Copy summary">Copy summary</button>
          <button type="button" class="fx-btn" data-u="py" data-label="Copy Python">Copy Python</button></div>
        <details><summary>Summary: the runs, the figure and where its data is (Copy summary copies it as Markdown)</summary>
          <div class="fx-md-view" id="fx-md-view"></div><pre class="fx-code" id="fx-md" hidden></pre></details>
        <details><summary>Python: plot the same thing (intake)</summary><pre class="fx-code" id="fx-py"></pre></details>
        <div class="fx-png"></div>`;
      const md = mdSummary(f, on, refs);
      $("#fx-md").textContent = md;
      $("#fx-md-view").innerHTML = mdHtml(md);
      $("#fx-py").textContent = pyScript(f, on);
    }
    async function exportPng(f, button) {
      const T = theme(), host = $(".fx-plots"), box = host.getBoundingClientRect(), s = 2, pad = 16;
      const legendItems = [...$(".fx-legend").querySelectorAll(".fx-it")];
      const titleH = 34, legendH = 18 * Math.ceil(legendItems.length / 3) + 8;
      const canvas = document.createElement("canvas");
      canvas.width = (box.width + 2 * pad) * s; canvas.height = (box.height + titleH + legendH + 2 * pad) * s;
      const ctx = canvas.getContext("2d");
      ctx.scale(s, s); ctx.fillStyle = T.bg; ctx.fillRect(0, 0, canvas.width, canvas.height);
      const font = getComputedStyle(root).fontFamily;
      ctx.fillStyle = T.ink; ctx.font = `600 18px ${font}`; ctx.fillText(f.display, pad, pad + 18);
      ctx.font = `12px ${font}`;  // legend: the page's own sample lines, drawn as images
      await Promise.all(legendItems.map(async (it, i) => {
        const x = pad + (i % 3) * (box.width / 3), y = pad + titleH + 18 * Math.floor(i / 3);
        const svg = it.querySelector("svg");
        if (svg) { const img = new Image(); img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(new XMLSerializer().serializeToString(svg));
          await img.decode().catch(() => {}); ctx.drawImage(img, x, y - 10, 26, 12); }
        ctx.fillStyle = T.ink; ctx.fillText(it.textContent.trim().slice(0, 60), x + 32, y);
      }));
      const top = pad + titleH + legendH;
      for (const h of host.querySelectorAll("h3")) { const r = h.getBoundingClientRect(); ctx.font = `600 12px ${font}`; ctx.fillText(h.textContent, pad + r.left - box.left, top + r.bottom - box.top - 4); }
      for (const pane of host.querySelectorAll(".fx-pane")) {
        const r = pane.getBoundingClientRect();
        const url = await window.Plotly.toImage(pane, { format: "png", width: r.width, height: r.height, scale: s });
        const img = new Image(); img.src = url; await img.decode();
        ctx.drawImage(img, pad + r.left - box.left, top + r.top - box.top, r.width, r.height);
      }
      const png = canvas.toDataURL("image/png"), name = `${f.notebook}_${f.display.replace(/[^\w]+/g, "_").slice(0, 40)}.png`;
      // In a claude.ai Artifact the viewer offers the file (downloads capability); on the site, a plain download.
      const downloads = window.claude?.use ? await window.claude.use("downloads").catch(() => null) : null;
      let said = "Exported";
      if (downloads) {
        const blob = await new Promise(ok => canvas.toBlob(ok, "image/png"));
        said = await downloads.save({ filename: name, data: blob }).then(() => "Saved", e => e?.code === "declined" ? "Not saved" : "Save unavailable: right-click the image below");
      } else {
        const a = document.createElement("a"); a.href = png; a.download = name; document.body.append(a); a.click(); a.remove();
      }
      $(".fx-png").innerHTML = `<p class="fx-k">${esc(name)}. You can also right-click the image to save it.</p><img alt="${esc(f.display)}, as exported" src="${png}">`;
      button.textContent = said; setTimeout(() => { button.textContent = button.dataset.label; }, 2500);
    }

    function notes(f, on) {
      const rows = state.runs.map(name => {
        const r = RUNS[name], d = r.provenance || {};
        const page = r.evaluation_url && f.runs[name] ? `${r.evaluation_url.replace(/\/?$/, "/")}experiments/${encodeURIComponent(name)}/${f.notebook}/` : "";
        return `<tr><th scope="row">${esc(name)}</th><td>${esc(r.forcing)}${r.forcing_mismatch ? ` <span class="fx-warn">(Base Configuration says ${esc(r.base_configuration)})</span>` : ""}</td>`
          + `<td>${esc(r.base_configuration)}</td><td>${esc(r.model_build)}</td><td>${esc(r.length)}</td><td>${esc(r.date_completed)}</td>`
          + `<td>${page ? `<a href="${esc(page)}">Evaluation page</a>` : ""}${r.figshare_article ? ` <a href="https://figshare.com/articles/figure/${r.figshare_article}">Figshare</a>` : ""}</td>`
          + `<td>${d.paper_commit ? `paper-1 ${esc(d.paper_commit.slice(0, 7))}, mkfigs ${esc(String(d.mkfigs_commit || "").slice(0, 12))}, ${esc((d.generated_at || "").slice(0, 10))}` : "no data yet"}</td></tr>`;
      }).join("");
      $(".fx-details-body").innerHTML = rows ? `<div class="fx-scroll"><table><thead><tr><th>Run</th><th>Forcing</th><th>Base configuration</th><th>Model build</th><th>Length</th><th>Completed</th><th>Pages</th><th>Data made by</th></tr></thead><tbody>${rows}</tbody></table></div>`
        + (state.runs.some(n => RUNS[n].description) ? state.runs.filter(n => RUNS[n].description).map(n => `<p><b>${esc(n)}</b>: ${esc(RUNS[n].description)}</p>`).join("") : "") : "<p>Select a run to see its details.</p>";
      $(".fx-notes").innerHTML = `<h3>About this figure</h3><p>${links(f.caption) || "No caption."}</p>`
        + `<p class="fx-k">Saved by <a href="${esc(NB[f.notebook]?.source || "#")}">${esc(f.notebook)}.ipynb</a> as figure ${esc(Object.values(f.runs)[0]?.fignum ?? "")}. Data built ${esc(M.generated_at.slice(0, 16).replace("T", " "))} UTC.</p>`;
    }

    function writeHash() {
      const p = new URLSearchParams({ fig: state.fig });
      if (state.runs.length) p.set("runs", state.runs.map(n => RUNS[n].id).join(","));
      if (state.xUser !== "auto") p.set("x", state.xUser);
      if (!state.raw) p.set("raw", "0");
      if (state.om2off.size) p.set("om2off", [...state.om2off].join(","));
      if (state.refoff.size) p.set("refoff", JSON.stringify([...state.refoff]));
      if (state.distinct) p.set("distinct", "1");
      history.replaceState(null, "", "#" + p.toString().replace(/%2C/g, ","));  // readable links: runs=a,b,c
    }
    const addRun = n => { if (state.runs.includes(n)) return; const used = new Set(slot.values()); let s = 0; while (used.has(s)) s++; slot.set(n, s); state.runs.push(n); };
    const dropRun = n => { state.runs = state.runs.filter(x => x !== n); slot.delete(n); };
    function show() { const f = FIG[state.fig]; header(f); runChips(f); controls(f, []); plots(f); writeHash(); }
    function select(id) {
      if (!FIG[id]) return;
      const cat = NB[FIG[id].notebook]?.category, wasOpen = state.open.has(cat);
      state.fig = id; state.hidden = new Set(FIG[id].hidden_series || []); state.open.add(cat);
      if (!wasOpen || state.q) rail();
      root.querySelectorAll(".fx-fig").forEach(b => b.setAttribute("aria-current", String(b.dataset.id === id)));
      show();
    }

    $(".fx-q").addEventListener("input", e => { state.q = e.target.value; rail(); });
    $(".fx-q").addEventListener("keydown", e => {
      if (e.key === "Enter") { const b = root.querySelector(".fx-fig"); if (b) select(b.dataset.id); }
      if (e.key === "Escape") { e.target.value = ""; state.q = ""; rail(); }
    });
    $(".fx-index").addEventListener("click", e => {
      const b = e.target.closest(".fx-fig"), c = e.target.closest(".fx-cat-h");
      if (b) select(b.dataset.id);
      else if (c && !state.q) { const n = c.dataset.cat; state.open.has(n) ? state.open.delete(n) : state.open.add(n); rail(); }
    });
    $(".fx-runs").addEventListener("change", e => {
      const t = e.target;
      if (t.dataset.om2) t.checked ? state.om2off.delete(t.dataset.om2) : state.om2off.add(t.dataset.om2);
      else if (t.dataset.ref !== undefined) t.checked ? state.refoff.delete(t.dataset.ref) : state.refoff.add(t.dataset.ref);
      else t.checked ? addRun(t.value) : dropRun(t.value);
      show();
    });
    $(".fx-use").addEventListener("click", async e => {
      const b = e.target.closest("button[data-u]");
      if (!b) return;
      if (b.dataset.u === "png") return exportPng(FIG[state.fig], b);
      const text = $(b.dataset.u === "md" ? "#fx-md" : "#fx-py").textContent;
      try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; }
      catch {  // clipboard refused: select the text instead (the Markdown is hidden, so show it first)
        const el = $(b.dataset.u === "md" ? "#fx-md" : "#fx-py"); el.hidden = false;
        const r = document.createRange(); r.selectNodeContents(el); getSelection().removeAllRanges(); getSelection().addRange(r); b.textContent = "Selected: press Ctrl+C"; }
      setTimeout(() => { b.textContent = b.dataset.label; }, 2000);
    });
    $(".fx-controls").addEventListener("change", e => {
      const t = e.target;
      if (t.name === "fx-x") state.xUser = t.value;
      else if (t.dataset.c === "raw") state.raw = t.checked;
      else if (t.dataset.c === "refs") state.refs = t.checked;
      else if (t.dataset.c === "distinct") state.distinct = t.checked;
      else if (t.dataset.series) t.checked ? state.hidden.delete(t.dataset.series) : state.hidden.add(t.dataset.series);
      show();
    });
    $(".fx-controls").addEventListener("click", async e => {
      if (e.target.dataset.c !== "copy") return;
      try { await navigator.clipboard.writeText(location.href); e.target.textContent = "Link copied"; } catch { e.target.textContent = "Copy from the address bar"; }
      setTimeout(() => { e.target.textContent = "Copy link"; }, 1800);
    });
    let rt;
    addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => plots(FIG[state.fig]), 150); });
    new MutationObserver(() => plots(FIG[state.fig])).observe(document.body, { attributes: true, attributeFilter: ["data-md-color-scheme"] });

    const h = new URLSearchParams(location.hash.slice(1));
    const drake = ORDER.find(id => /drake passage/i.test(FIG[id].display));
    state.fig = FIG[h.get("fig")] ? h.get("fig") : (drake || ORDER[0]);
    state.hidden = new Set(FIG[state.fig].hidden_series || []);
    const linked = (h.get("runs") || "").split(",").map(id => BY_ID[id]).filter(Boolean);
    (linked.length ? linked : M.runs.filter(r => FIG[state.fig].runs[r.name]).slice(0, 3).map(r => r.name)).forEach(addRun);
    state.xUser = ["date", "years"].includes(h.get("x")) ? h.get("x") : "auto";
    state.raw = h.get("raw") !== "0";
    state.refs = h.get("refs") !== "0";
    (h.get("om2off") || "").split(",").filter(k => OM2_NAME[k]).forEach(k => state.om2off.add(k));
    try { JSON.parse(h.get("refoff") || "[]").forEach(n => typeof n === "string" && state.refoff.add(n)); } catch { /* an old or edited link */ }
    state.distinct = h.get("distinct") === "1";
    state.open.add(NB[FIG[state.fig].notebook]?.category);
    key(); rail(); show();
  }

  // navigation.instant swaps pages without reloading scripts, so run on every page view.
  typeof document$ !== "undefined" ? document$.subscribe(init) : document.addEventListener("DOMContentLoaded", init);
})();
