// Figure explorer: overlay line-plot figures from runs on the Experiments page.
// Data comes from scripts/figure_explorer_data.py; plotly.js loads only on this page.
(() => {
  const PLOTLY = "https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.32.0/plotly.min.js";
  const DASH = ["solid", "dash", "dot", "dashdot", "longdash"];
  const DASHARRAY = { solid: "", dash: "6 4", dot: "1.6 3", dashdot: "6 3 1.6 3", longdash: "11 4" };
  const LIGHT = ["#0072b2", "#d55e00", "#009e73", "#cc79a7", "#8c6d1f", "#3a9bd9", "#7a5195", "#c23b3e"];
  const DARK = ["#5aa9e6", "#f28e4c", "#3cc49c", "#e59ac8", "#d9b45b", "#9bd5f5", "#b296da", "#f07b7d"];
  const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const links = s => esc(s).replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2">$1</a>');
  const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const json = url => fetch(url).then(r => { if (!r.ok) throw new Error(`${r.status} ${url}`); return r.json(); });
  let plotly = null;
  const loadPlotly = () => plotly ??= window.Plotly ? Promise.resolve() : new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = PLOTLY; s.onload = ok; s.onerror = () => fail(new Error("plotly.js did not load"));
    document.head.append(s);
  });

  const TEMPLATE = `<div class="fx">
    <aside class="fx-rail"><label class="fx-vh" for="fx-q">Search figures</label>
      <input id="fx-q" class="fx-q" type="search" placeholder="Search figures" autocomplete="off" spellcheck="false">
      <p class="fx-count" aria-live="polite"></p><nav class="fx-index"></nav></aside>
    <div class="fx-main"><div class="fx-crumbs"></div><h2 class="fx-title"></h2><p class="fx-sub"></p>
      <div class="fx-runs"></div><div class="fx-controls"></div><div class="fx-plots"></div><div class="fx-legend"></div>
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
    const state = { fig: null, runs: [], xUser: "auto", raw: true, refs: true, hidden: new Set(), q: "", open: new Set() };
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
        ref: dark ? "#9aa3a9" : "#737b80", pal: dark ? DARK : LIGHT };
    };
    const colour = (n, T) => T.pal[slot.get(n) % T.pal.length];
    const yrs = (d, start) => { const [Y, Mo, D] = d.slice(0, 10).split("-").map(Number); return +(Y - start + ((Mo - 1) * 30.44 + D) / 365.25).toFixed(3); };
    const dashOf = (name, names) => name ? DASH[Math.max(0, names.indexOf(name)) % DASH.length] : "solid";
    const sample = (c, dash, w, marker) => marker
      ? `<svg width="26" height="12" aria-hidden="true"><path d="M9 2 L17 10 M17 2 L9 10" stroke="${c}" stroke-width="1.6"/></svg>`
      : `<svg width="26" height="12" aria-hidden="true"><line x1="1" y1="6" x2="25" y2="6" stroke="${c}" stroke-width="${w}" stroke-dasharray="${DASHARRAY[dash] || ""}"/></svg>`;
    const period = r => [r.forcing, r.length].filter(Boolean).join(", ");

    // ---- search index ----
    const HAY = {};
    for (const id of ORDER) {
      const f = FIG[id], nb = NB[f.notebook];
      HAY[id] = [f.display, f.subtitle, f.notebook, nb?.category, f.caption, ...f.panels.map(p => `${p.title} ${p.y_label}`)]
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
              return `<li><button type="button" class="fx-fig" data-id="${id}" aria-current="${id === state.fig}"><span class="fx-fl">${hl(f.display, ts)}</span><span class="fx-n" title="Data for ${k} of ${M.runs.length} runs">${k}</span>${f.subtitle ? `<span class="fx-fs">${hl(f.subtitle, ts)}</span>` : ""}</button></li>`; }).join("")}</ul></div>`;
        }).join("");
        shown += n;
        if (!n) return "";
        const open = ts.length > 0 || state.open.has(c.name);
        return `<div class="fx-cat" data-open="${open ? 1 : 0}"><button type="button" class="fx-cat-h" data-cat="${esc(c.name)}" aria-expanded="${open}"><span class="fx-caret" aria-hidden="true">▸</span><span>${hl(c.name, ts)}</span><span class="fx-n">${n}</span></button><div class="fx-cat-b">${nbs}</div></div>`;
      }).join("") || `<p>No figures match “${esc(state.q)}”. Try a variable such as thetaoga, a place such as Drake, or a notebook name.</p>`;
      $(".fx-count").textContent = ts.length ? `${shown} of ${ORDER.length} figures match` : `${ORDER.length} line-plot figures. Try Drake, thetaoga or AMOC.`;
    }

    // ---- main column ----
    function header(f) {
      const nb = NB[f.notebook];
      $(".fx-crumbs").innerHTML = `${esc(nb?.category || "")} › <a href="${esc(nb?.source || "#")}">${esc(f.notebook)}.ipynb</a>`;
      $(".fx-title").textContent = f.display;
      $(".fx-sub").textContent = f.subtitle;
    }
    function runChips(f) {
      $(".fx-runs").innerHTML = GROUPS.map(g => `<div class="fx-grp"><div class="fx-grp-h">${esc(g)}</div><div class="fx-chips">${M.runs.filter(r => r.group === g).map(r => {
        const on = state.runs.includes(r.name), has = !!f.runs[r.name];
        const tip = has ? `${period(r)}${r.forcing_mismatch ? `. Base Configuration says ${r.base_configuration}` : ""}` : (r.has_data ? "No line data for this figure" : "No figure data for this run yet");
        return `<label class="fx-chip${on ? " on" : ""}${has ? "" : " na"}"${on ? ` style="--fx-chip:${colour(r.name, theme())}"` : ""} title="${esc(tip)}"><input type="checkbox" value="${esc(r.name)}"${on ? " checked" : ""}><span class="fx-sw"></span><span class="fx-nm">${esc(r.name)}</span><span class="fx-badge${r.forcing_mismatch ? " warn" : ""}">${esc(r.forcing)}</span></label>`;
      }).join("")}</div></div>`).join("");
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
        `<label class="fx-ctl"><input type="checkbox" data-c="refs"${state.refs ? " checked" : ""}>References</label>` +
        (names.length ? `<span class="fx-series">${names.map(n => `<label class="fx-chip${state.hidden.has(n) ? "" : " on"}" style="--fx-chip:currentColor"><input type="checkbox" data-series="${esc(n)}"${state.hidden.has(n) ? "" : " checked"}>${sample("currentColor", dashOf(n, names), 1.7)}<span>${esc(n)}</span></label>`).join("")}</span>` : "") +
        `<button type="button" class="fx-btn" data-c="copy">Copy link</button>` + (why ? `<span class="fx-why">${why}</span>` : "");
    }

    async function plots(f) {
      const my = ++version, T = theme(), host = $(".fx-plots"), on = active(f), mode = axis(f);
      const [datas, refs] = await Promise.all([Promise.all(on.map(n => load(f.id, n))), state.refs ? loadRefs(f.id) : []]);
      if (my !== version) return;
      const names = [...new Set(datas.flatMap(d => d.panels.flatMap(p => p.lines.map(l => l.name).filter(Boolean))))];
      f._hasThin = datas.some(d => d.panels.some(p => p.lines.some(l => l.style === "thin")));
      controls(f, names);
      if (!on.length) {
        host.innerHTML = `<div class="fx-empty">${state.runs.length ? "None of the selected runs has this figure; runs with a solid outline do." : "Choose one or more runs above."}</div>`;
        legend(f, on, [], names, T); notes(f, on); return;
      }
      const n = f.panels.length, wide = host.clientWidth;
      host.style.setProperty("--fx-cols", n === 1 ? 1 : (n === 3 && wide > 1020 ? 3 : (wide > 620 ? 2 : 1)));
      host.style.setProperty("--fx-h", n === 1 ? "420px" : "300px");
      if (host.dataset.fig !== f.id) {
        host.querySelectorAll(".fx-pane").forEach(d => window.Plotly.purge(d));
        host.innerHTML = f.panels.map((p, i) => `<div>${n > 1 ? `<h3>${esc(p.title)}</h3>` : ""}<div class="fx-pane" data-i="${i}"></div></div>`).join("");
        host.dataset.fig = f.id;
      }
      f.panels.forEach((p, i) => {
        const traces = [], shapes = [], ann = [];
        on.forEach((name, k) => {
          const c = colour(name, T), start = RUNS[name].start_year;
          for (const s of datas[k].panels[i]?.lines || []) {
            if ((s.name && state.hidden.has(s.name)) || (s.style === "thin" && !state.raw)) continue;
            const thin = s.style === "thin", pts = s.style === "points";
            traces.push({ type: "scatter", mode: pts ? "markers" : "lines", x: mode === "years" ? s.x.map(d => yrs(d, start ?? +d.slice(0, 4))) : s.x, y: s.y,
              line: { color: c, width: thin ? 0.8 : 1.9, dash: dashOf(s.name, names) }, marker: { color: c, size: 6 }, opacity: thin ? 0.35 : 1,
              hoverinfo: thin ? "skip" : undefined, hovertemplate: thin ? undefined : `<b>${esc(name)}</b>${s.name ? " – " + esc(s.name) : ""}<br>%{x}<br>%{y:.4~g}<extra></extra>` });
          }
        });
        let k = 0;
        const x0 = traces.find(t => t.x?.length)?.x[0];
        for (const r of refs.filter(r => r.panel === i)) {
          if (r.kind === "line" || r.kind === "points") {
            if (r.kind === "line" && isTime(f) && mode === "years" && !r.x[0]) continue;
            traces.push({ type: "scatter", mode: r.kind === "points" ? "markers" : "lines", x: mode === "years" ? r.x.map(d => yrs(d, +r.x[0].slice(0, 4))) : r.x, y: r.y,
              line: { color: T.ref, width: 1.4, dash: DASH[k++ % 3] }, marker: { color: T.ref, size: 7, symbol: "x-thin", line: { width: 1.6, color: T.ref } },
              hovertemplate: `<b>${esc(r.name)}</b><br>%{x}<br>%{y:.4~g}<extra>reference</extra>` });
          } else if (r.kind === "hline") {
            shapes.push({ type: "line", xref: "paper", x0: 0, x1: 1, y0: r.y, y1: r.y, line: { color: T.ink, width: 1.3, dash: "dash" } });
            ann.push({ xref: "paper", x: 0.005, xanchor: "left", y: r.y, yanchor: "bottom", yshift: 2, showarrow: false, bgcolor: T.bg, opacity: 0.92,
              text: esc(r.name) + (mode === "years" ? " (observed-period mean)" : ""), font: { size: 11, color: T.ink } });
            if (x0 !== undefined) traces.push({ type: "scatter", mode: "markers", x: [x0], y: [r.y], marker: { opacity: 0 }, hoverinfo: "skip" });
          } else if (r.kind === "band") {
            shapes.push({ type: "rect", xref: "paper", x0: 0, x1: 1, y0: r.y[0], y1: r.y[1], fillcolor: T.ref, opacity: 0.09, line: { width: 0 }, layer: "below" });
          } else if (r.kind === "span" && mode === "date") {
            shapes.push({ type: "rect", xref: "x", yref: "paper", x0: r.x[0], x1: r.x[1], y0: 0, y1: 1, fillcolor: T.ref, opacity: 0.13, line: { width: 0 }, layer: "below" });
          }
        }
        const ax = { gridcolor: T.rule, linecolor: T.rule, zerolinecolor: T.rule, tickfont: { size: 11 } };
        const xTitle = isTime(f) ? (mode === "years" ? "Years since start of run" : "Model date") : (p.x_label || "");
        const months = f.x_kind !== "time" && /month/i.test(p.x_label || "") ? { tickmode: "array", tickvals: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], ticktext: MONTHS } : {};
        window.Plotly.react(host.querySelector(`.fx-pane[data-i="${i}"]`), traces, {
          margin: { l: 66, r: 14, t: 8, b: 42 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", showlegend: false, hovermode: "closest",
          font: { family: getComputedStyle(root).fontFamily, size: 12, color: T.ink }, hoverlabel: { bgcolor: T.bg, bordercolor: T.rule, font: { color: T.ink } },
          xaxis: { ...ax, ...months, type: isTime(f) && mode === "date" ? "date" : "linear", title: { text: xTitle, font: { size: 12, color: T.muted } } },
          yaxis: { ...ax, type: p.y_log ? "log" : "linear", exponentformat: "power", automargin: true, title: { text: p.y_label, font: { size: 12, color: T.muted } } },
          shapes, annotations: ann, uirevision: f.id + mode,
        }, { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "toggleSpikelines"] });
      });
      legend(f, on, refs, names, T); notes(f, on);
    }

    function legend(f, on, refs, names, T) {
      const items = on.map(name => {
        const r = RUNS[name], avg = (f.runs[name].periods || []).join("; ");
        return `<span class="fx-it">${sample(colour(name, T), "solid", 2.2)}<span><b>${esc(name)}</b> <span class="fx-k">${esc(period(r))}${avg ? `; averaging ${esc(avg)}` : ""}</span></span></span>`;
      });
      names.forEach(n => items.push(`<span class="fx-it">${sample(T.ink, dashOf(n, names), 1.7)}<span>${esc(n)}</span></span>`));
      if (on.length && state.raw && f._hasThin) items.push('<span class="fx-it fx-k">Thin lines: monthly values. Thick lines: as drawn in the notebook (e.g. a running mean).</span>');
      if (state.refs) {
        let k = 0;
        const seen = new Set();
        for (const r of refs) {
          if (!r.name || seen.has(r.name)) continue;
          seen.add(r.name);
          const s = r.kind === "points" ? sample(T.ref, "", 0, true) : r.kind === "hline" ? sample(T.ink, "dash", 1.3) : sample(T.ref, r.kind === "line" ? DASH[k++ % 3] : "solid", 1.5);
          items.push(`<span class="fx-it">${s}<span class="fx-k">${esc(r.name)}</span></span>`);
        }
      }
      $(".fx-legend").innerHTML = items.join("");
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
      if (!state.refs) p.set("refs", "0");
      history.replaceState(null, "", "#" + p.toString());
    }
    const addRun = n => { if (state.runs.includes(n)) return; const used = new Set(slot.values()); let s = 0; while (used.has(s)) s++; slot.set(n, s); state.runs.push(n); };
    const dropRun = n => { state.runs = state.runs.filter(x => x !== n); slot.delete(n); };
    function show() { const f = FIG[state.fig]; header(f); runChips(f); controls(f, []); plots(f); writeHash(); }
    function select(id) {
      if (!FIG[id]) return;
      const cat = NB[FIG[id].notebook]?.category, wasOpen = state.open.has(cat);
      state.fig = id; state.hidden.clear(); state.open.add(cat);
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
    $(".fx-runs").addEventListener("change", e => { e.target.checked ? addRun(e.target.value) : dropRun(e.target.value); show(); });
    $(".fx-controls").addEventListener("change", e => {
      const t = e.target;
      if (t.name === "fx-x") state.xUser = t.value;
      else if (t.dataset.c === "raw") state.raw = t.checked;
      else if (t.dataset.c === "refs") state.refs = t.checked;
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
    const linked = (h.get("runs") || "").split(",").map(id => BY_ID[id]).filter(Boolean);
    (linked.length ? linked : M.runs.filter(r => FIG[state.fig].runs[r.name]).slice(0, 3).map(r => r.name)).forEach(addRun);
    state.xUser = ["date", "years"].includes(h.get("x")) ? h.get("x") : "auto";
    state.raw = h.get("raw") !== "0";
    state.refs = h.get("refs") !== "0";
    state.open.add(NB[FIG[state.fig].notebook]?.category);
    rail(); show();
  }

  // navigation.instant swaps pages without reloading scripts, so run on every page view.
  typeof document$ !== "undefined" ? document$.subscribe(init) : document.addEventListener("DOMContentLoaded", init);
})();
