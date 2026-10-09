// Downloads page: renders the release list from OTC_index.json on the data host, newest
// first, one entry per release in the style of a GitHub release: the release stem and date,
// what changed, and the files with their sizes and SHA-256 checksums.
// Index entries carry: datestamp, created, format_version, concept_doi, zenodo_version_doi
// (null when the release has no Zenodo version of its own), files (a list of
// { url, size, sha256 }, or an object keyed by file name), and optionally changes (a list of
// short strings), source_versions ({ source: version }) and changelog_url.
// Everything from the index is written with textContent; links must be http(s).
(() => {
  // The Formats and Locations panels start closed; a link to one of them opens it.
  const openTarget = () => {
    const d = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
    if (d && d.tagName === "DETAILS") d.open = true;
  };
  openTarget();
  addEventListener("hashchange", openTarget);

  const box = document.querySelector(".releases[data-index]");
  if (!box) return;

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  };
  const safeUrl = (u) => { try { const x = new URL(u, box.dataset.index); return /^https?:$/.test(x.protocol) ? x.href : null; } catch { return null; } };
  const link = (href, text) => { const u = safeUrl(href); if (!u) return el("span", null, text); const a = el("a", null, text); a.href = u; return a; };
  const size = (n) => {
    if (typeof n !== "number" || !isFinite(n)) return "";
    const units = ["B", "KB", "MB", "GB"]; let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
  };
  const day = (r) => {
    const d = String(r.datestamp ?? "").slice(0, 8);
    return /^\d{8}$/.test(d) ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}` : "";
  };
  const fileList = (files) => {
    if (Array.isArray(files)) return files;
    if (files && typeof files === "object") return Object.entries(files).map(([name, f]) => ({ name, ...(typeof f === "object" ? f : { url: f }) }));
    return [];
  };

  const render = (releases) => {
    box.replaceChildren();
    if (!releases.length) { box.append(el("p", null, "No releases are listed in the index.")); return; }
    releases.forEach((r, i) => {
      const art = el("article", "rel");
      const head = el("div", "rel-head");
      const h3 = el("h3", null, `OTC_${r.datestamp}`);
      h3.id = `r${String(r.datestamp).replace(/\W/g, "-")}`;
      head.append(h3);
      if (i === 0) head.append(el("span", "tag", "Latest"));
      art.append(head);

      const meta = el("p", "rel-meta");
      const bits = [day(r), r.format_version ? `format ${r.format_version}` : ""].filter(Boolean);
      meta.append(bits.join(" · "));
      // The release's own Zenodo version DOI; without one, the concept DOI, labelled as such.
      if (typeof r.zenodo_version_doi === "string") {
        meta.append(bits.length ? " · " : "", link(`https://doi.org/${r.zenodo_version_doi}`, `doi:${r.zenodo_version_doi}`));
      } else if (typeof r.concept_doi === "string") {
        meta.append(bits.length ? " · " : "", link(`https://doi.org/${r.concept_doi}`, `concept doi:${r.concept_doi}`));
      }
      art.append(meta);

      const changes = Array.isArray(r.changes) ? r.changes : [];
      if (changes.length) {
        art.append(el("h4", null, "Changes"));
        const ul = el("ul");
        changes.forEach((c) => ul.append(el("li", null, c)));
        art.append(ul);
      }
      if (r.source_versions && typeof r.source_versions === "object") {
        const v = Object.entries(r.source_versions).map(([k, x]) => `${k} ${x}`).join(", ");
        if (v) art.append(el("p", "rel-sources", `Sources: ${v}`));
      }
      if (r.changelog_url) { const p = el("p"); p.append(link(r.changelog_url, "Full change list")); art.append(p); }

      const files = fileList(r.files);
      if (files.length) {
        const d = el("details", "rel-files");
        if (i === 0) d.open = true;
        d.append(el("summary", null, `Files (${files.length})`));
        const ul = el("ul");
        files.forEach((f) => {
          const li = el("li");
          const name = f.name ?? String(f.url ?? "").split("/").pop();
          li.append(link(f.url ?? name, name), el("span", "rel-size", size(f.size)));
          if (f.sha256) li.append(el("code", "rel-sha", f.sha256));
          ul.append(li);
        });
        d.append(ul);
        art.append(d);
      }
      box.append(art);
    });
  };

  fetch(box.dataset.index, { cache: "no-cache" })
    .then((res) => (res.status === 404 ? [] : res.ok ? res.json() : Promise.reject(res.status)))
    .then((j) => render(Array.isArray(j) ? j : Array.isArray(j?.releases) ? j.releases : []))
    .catch(() => {});   // the static text with the index link stays
})();
