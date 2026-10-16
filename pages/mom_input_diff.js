// MOM_input diff page: two experiment dropdowns, live filter, shareable #a=...&b=... links.
(() => {
  const REPO = "https://github.com/ACCESS-Community-Hub/access-om3-experiments";
  const MOM6 = "https://github.com/mom-ocean/MOM6/search?q=";
  const cache = {};
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const load = (base, slug) => cache[slug] ??= fetch(base + slug + ".json").then(r => r.json());
  const flat = cfg => Object.fromEntries(Object.entries(cfg).flatMap(([m, vs]) => Object.entries(vs).map(([v, p]) => [v, { ...p, module: m }])));

  function diff(A, B) {
    const a = flat(A), b = flat(B);
    return [...new Set([...Object.keys(a), ...Object.keys(b)])].sort().map(v => {
      const module = [a[v]?.module, b[v]?.module].find(m => m && m !== "MOM_override") || "MOM_override";
      const dflt = a[v]?.default || b[v]?.default || null;
      return { v, module, units: a[v]?.units || b[v]?.units || "", dflt, a: a[v], b: b[v], differs: a[v]?.value !== b[v]?.value };
    }).sort((x, y) => x.module.localeCompare(y.module) || x.v.localeCompare(y.v));
  }

  async function init() {
    const root = document.getElementById("mom-diff");
    if (!root || root.dataset.ready) return;
    root.dataset.ready = "1";
    const base = root.dataset.base, index = await (await fetch(base + "index.json")).json();
    const hash = new URLSearchParams(location.hash.slice(1));
    const opts = index.map((e, i) => ({ g: e.group, h: `<option value="${i}">${esc(e.label)}</option>` }));
    const sel = (id, cls, label) => `<div class="${cls}"><label for="${id}">${label}</label><select id="${id}">${
      [...new Set(opts.map(o => o.g))].map(g => `<optgroup label="${esc(g)}">${opts.filter(o => o.g === g).map(o => o.h).join("")}</optgroup>`).join("")}</select></div>`;
    root.innerHTML = `<div class="bar">${sel("mda", "a", "Experiment A")}<button id="mdswap" title="Swap A and B">⇄</button>${sel("mdb", "b", "Experiment B")}
      <div><label for="mdf">Filter</label><input type="text" id="mdf" placeholder="variable, module or value"></div>
      <div><label><input type="checkbox" id="mdall"> show unchanged</label></div><button id="mdcopy">Copy as Markdown</button></div>
      <div class="refs" id="mdrefs"></div><div class="count" id="mdcount"></div><div id="mdtable"></div>`;
    const $ = id => document.getElementById(id);
    const pick = (key, dflt) => { const i = index.findIndex(e => e.label === hash.get(key)); return i >= 0 ? i : dflt; };
    $("mda").value = pick("a", 0);
    $("mdb").value = pick("b", Math.min(1, index.length - 1));
    let rows = [], ea, eb;

    const visible = () => { const q = $("mdf").value.trim().toLowerCase();
      return rows.filter(r => ($("mdall").checked || r.differs) && (!q || [r.module, r.v, r.a?.value, r.b?.value].join(" ").toLowerCase().includes(q))); };
    const cell = (r, p, e, cls) => p
      ? `<td class="${cls}"><a href="${REPO}/blob/${e.sha}/${p.file || "MOM_input"}#L${p.line}" target="_blank" rel="noopener" title="${esc(p.file || "MOM_input")} line ${p.line}">${esc(p.value)}</a></td>`
      : `<td class="${cls} unset" title="Not set here: MOM6 uses its default">(default${r.dflt ? ": " + esc(r.dflt) : ""})</td>`;
    const draw = () => {
      const vis = visible(); let mod = "", html = "";
      for (const r of vis) {
        if (r.module !== mod) { mod = r.module; html += `<tr class="mod"><td colspan="4"><a href="${MOM6}${encodeURIComponent(mod)}" target="_blank" rel="noopener">${esc(mod)}</a></td></tr>`; }
        html += `<tr><td><a href="${MOM6}${encodeURIComponent(r.v)}" target="_blank" rel="noopener">${esc(r.v)}</a></td><td>${esc(r.units)}</td>${cell(r, r.a, ea, "a")}${cell(r, r.b, eb, "b")}</tr>`;
      }
      $("mdtable").innerHTML = vis.length ? `<table><thead><tr><th>Variable</th><th>Units</th><th class="a">${esc(ea.label)}</th><th class="b">${esc(eb.label)}</th></tr></thead><tbody>${html}</tbody></table>`
        : "<p>No differences.</p>";
      $("mdcount").textContent = `${rows.filter(r => r.differs).length} of ${rows.length} parameters differ; ${vis.length} shown`;
    };
    const update = async () => {
      ea = index[$("mda").value]; eb = index[$("mdb").value];
      history.replaceState(null, "", `#a=${encodeURIComponent(ea.label)}&b=${encodeURIComponent(eb.label)}`);
      $("mdrefs").innerHTML = [ea, eb].map(e => `<a href="${REPO}/tree/${e.sha}" target="_blank" rel="noopener">${esc(e.label)}</a> @ <code>${e.sha.slice(0, 10)}</code>`).join(" vs ");
      rows = diff(...await Promise.all([load(base, ea.slug), load(base, eb.slug)]));
      draw();
    };
    $("mda").onchange = $("mdb").onchange = update;
    $("mdall").onchange = $("mdf").oninput = draw;
    $("mdswap").onclick = () => { [$("mda").value, $("mdb").value] = [$("mdb").value, $("mda").value]; update(); };
    $("mdcopy").onclick = async () => {
      const v = p => p ? p.value : "_(default)_";
      await navigator.clipboard.writeText([`| Module | Variable | ${ea.label} | ${eb.label} |`, "|---|---|---|---|",
        ...visible().map(r => `| ${r.module} | \`${r.v}\` | ${v(r.a)} | ${v(r.b)} |`)].join("\n"));
      $("mdcopy").textContent = "Copied!"; setTimeout(() => $("mdcopy").textContent = "Copy as Markdown", 1500);
    };
    update();
  }
  // navigation.instant swaps pages without reloading scripts, so run on every page view.
  typeof document$ !== "undefined" ? document$.subscribe(init) : document.addEventListener("DOMContentLoaded", init);
})();
