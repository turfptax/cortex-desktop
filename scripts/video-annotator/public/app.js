// Cortex Video Annotator — frontend.
// State machine: list of videos in `app.videos`, currently selected
// in `app.currentId`. Filters re-fetch via /api/videos. Annotation
// edits are saved per-field on blur OR via the explicit Save button
// (Ctrl+S). Decision buttons save immediately so single-keystroke
// triage is fast.

const app = {
  videos: [],
  currentId: null,
  currentIndex: -1,
  filterDebounce: null,

  async init() {
    await this.loadStats();
    await this.loadVideos();
    this.setupKeyboard();
  },

  // ── Stats / filters ──────────────────────────────────────────
  async loadStats() {
    const res = await fetch("/api/stats");
    const stats = await res.json();
    const decisions = Object.fromEntries(stats.byDecision.map((d) => [d.decision, d.n]));
    const html = [
      `Total: <strong>${stats.total}</strong>`,
      `Mine: <strong style="color:var(--accent)">${decisions.mine || 0}</strong>`,
      `Skip: <strong>${decisions.skip || 0}</strong>`,
      `Unsure: <strong style="color:var(--blue)">${decisions.unsure || 0}</strong>`,
      `Pending: <strong>${decisions.pending || 0}</strong>`,
      `Transcribed: <strong style="color:var(--green)">${stats.transcribed}</strong>`,
      `Partial: <strong style="color:var(--yellow)">${stats.partial}</strong>`,
      `Errored: <strong style="color:var(--red)">${stats.errored}</strong>`,
    ].join(" · ");
    document.getElementById("stats").innerHTML = html;

    // Year filter dropdown — repopulate from current data
    const yearSelect = document.getElementById("filter-year");
    const currentYear = yearSelect.value;
    yearSelect.innerHTML = '<option value="">All years</option>';
    for (const y of stats.byYear) {
      if (!y.year) continue;
      yearSelect.innerHTML += `<option value="${y.year}">${y.year} (${y.n})</option>`;
    }
    yearSelect.value = currentYear;
  },

  async loadVideos() {
    const params = new URLSearchParams();
    const decision = document.getElementById("filter-decision").value;
    const year = document.getElementById("filter-year").value;
    const transcribed = document.getElementById("filter-transcribed").value;
    const sort = document.getElementById("filter-sort").value;
    const search = document.getElementById("search").value.trim();
    if (decision) params.set("decision", decision);
    if (year) params.set("year", year);
    if (transcribed !== "") params.set("transcribed", transcribed);
    if (sort) params.set("sort", sort);
    if (search) params.set("search", search);

    const res = await fetch("/api/videos?" + params.toString());
    this.videos = await res.json();
    this.renderList();
    this.updatePosition();
  },

  applyFilters() {
    this.loadVideos();
  },
  debouncedFilter() {
    clearTimeout(this.filterDebounce);
    this.filterDebounce = setTimeout(() => this.loadVideos(), 200);
  },

  renderList() {
    const list = document.getElementById("video-list");
    if (this.videos.length === 0) {
      list.innerHTML = '<div style="padding:20px;color:var(--text-dim);text-align:center">No videos match filters.</div>';
      return;
    }
    list.innerHTML = this.videos.map((v, i) => {
      const badges = [];
      if (v.decision && v.decision !== "pending")
        badges.push(`<span class="badge ${v.decision}">${v.decision}</span>`);
      if (v.transcribe_done) badges.push('<span class="badge transcribed">tx</span>');
      else if (v.transcribe_partial) badges.push('<span class="badge partial">partial</span>');
      else if (v.transcribe_error) badges.push('<span class="badge error">err</span>');
      const dur = v.duration_s ? this.fmtDur(v.duration_s) : "";
      const sz = v.file_size ? this.fmtSize(v.file_size) : "";
      const meta = [v.filmed_date || "", dur, sz].filter(Boolean).join(" · ");
      return `
        <div class="video-item ${v.id === this.currentId ? "active" : ""}"
             onclick="app.selectVideo(${v.id}, ${i})">
          <img src="/api/videos/${v.id}/thumb/1" loading="lazy" onerror="this.style.opacity=0.2">
          <div class="item-info">
            <div class="item-name">${this.escape(v.title || v.filename)}</div>
            <div class="item-meta">${badges.join("")} ${this.escape(meta)}</div>
          </div>
        </div>`;
    }).join("");
  },

  updatePosition() {
    document.getElementById("position-info").textContent =
      this.currentIndex >= 0
        ? `${this.currentIndex + 1} / ${this.videos.length}`
        : `${this.videos.length} videos`;
  },

  // ── Selection ────────────────────────────────────────────────
  async selectVideo(id, index) {
    this.currentId = id;
    this.currentIndex = index;
    document.querySelectorAll(".video-item").forEach((el, i) => {
      el.classList.toggle("active", this.videos[i] && this.videos[i].id === id);
    });
    this.updatePosition();
    const res = await fetch(`/api/videos/${id}`);
    const v = await res.json();
    this.renderViewer(v);
    this.renderPanel(v);
  },

  renderViewer(v) {
    const viewer = document.getElementById("viewer");
    let transcriptHtml = "";
    // Transcript is loaded async since the file may be large
    viewer.innerHTML = `
      <video class="video-player" controls preload="metadata"
             src="/api/videos/${v.id}/stream"></video>
      <div class="thumb-strip">
        <img src="/api/videos/${v.id}/thumb/0" alt="start" onerror="this.style.opacity=0.2">
        <img src="/api/videos/${v.id}/thumb/1" alt="middle" onerror="this.style.opacity=0.2">
        <img src="/api/videos/${v.id}/thumb/2" alt="end" onerror="this.style.opacity=0.2">
      </div>
      <div class="transcript-block" id="transcript-block">Loading transcript…</div>
    `;
    this.loadTranscript(v.id);
  },

  async loadTranscript(id) {
    const block = document.getElementById("transcript-block");
    if (!block) return;
    try {
      const res = await fetch(`/api/videos/${id}/transcript`);
      if (res.status === 404) {
        block.classList.add("empty");
        block.textContent = "(no transcript yet — file hasn't been transcribed, or the run errored on this video)";
        return;
      }
      const text = await res.text();
      if (text && text.trim()) {
        block.classList.remove("empty");
        block.textContent = text;
      } else {
        block.classList.add("empty");
        block.textContent = "(empty transcript)";
      }
    } catch (e) {
      block.classList.add("empty");
      block.textContent = "Error loading transcript: " + e.message;
    }
  },

  renderPanel(v) {
    const panel = document.getElementById("panel");
    const rel = v.rel_path || v.filename;
    const dur = v.duration_s ? this.fmtDur(v.duration_s) : "—";
    const sz = v.file_size ? this.fmtSize(v.file_size) : "—";
    const stateBadges = [];
    if (v.transcribe_done) stateBadges.push('<span class="badge transcribed">transcribed</span>');
    if (v.transcribe_partial) stateBadges.push('<span class="badge partial">partial</span>');
    if (v.transcribe_error) stateBadges.push('<span class="badge error">errored</span>');
    if (!stateBadges.length) stateBadges.push('<span style="color:var(--text-dim)">not yet</span>');

    panel.innerHTML = `
      <div>
        <h3>File</h3>
        <div class="meta">
          <div><strong>Path:</strong> <span class="copyable" onclick="app.copyText('${this.escAttr(v.src_path)}')">${this.escape(rel)}</span></div>
          <div style="margin-top:6px"><strong>Folder:</strong> <span class="copyable" onclick="app.copyText('${this.escAttr(v.parent_dir)}')">${this.escape(v.parent_dir)}</span></div>
          <div style="margin-top:6px">
            <strong>Size:</strong> ${sz} ·
            <strong>Duration:</strong> ${dur} ·
            <strong>Filmed:</strong> ${v.filmed_date || "?"}
            ${v.filmed_date_source ? `<span style="opacity:0.5">(${v.filmed_date_source})</span>` : ""}
          </div>
          <div style="margin-top:6px"><strong>State:</strong> ${stateBadges.join(" ")} ${v.transcript_chars ? `<span style="opacity:0.6">${v.transcript_chars}c</span>` : ""}</div>
        </div>
      </div>

      <div class="row">
        <button class="btn" onclick="app.openInExplorer(${v.id})">📁 Show in Explorer</button>
        <button class="btn" onclick="app.playInDefaultApp(${v.id})">🎬 Open in player</button>
      </div>

      <div>
        <h3>Decision</h3>
        <div class="decision-row">
          <button class="decision-btn ${v.decision === "mine" ? "active mine" : ""}" onclick="app.setDecision('mine')">Mine</button>
          <button class="decision-btn ${v.decision === "skip" ? "active skip" : ""}" onclick="app.setDecision('skip')">Skip</button>
          <button class="decision-btn ${v.decision === "unsure" ? "active unsure" : ""}" onclick="app.setDecision('unsure')">Unsure</button>
          <button class="decision-btn ${v.decision === "pending" || !v.decision ? "active pending" : ""}" onclick="app.setDecision('pending')">Pending</button>
        </div>
      </div>

      <div>
        <h3>Annotation</h3>
        <label>Title</label>
        <input type="text" id="ann-title" value="${this.escAttr(v.title || "")}" placeholder="${this.escAttr(v.filename)}" onblur="app.saveField('title', this.value)">
        <label style="margin-top:10px">Description</label>
        <textarea id="ann-description" placeholder="What's this video about?" onblur="app.saveField('description', this.value)">${this.escape(v.description || "")}</textarea>
        <label style="margin-top:10px">Tags (comma-separated)</label>
        <input type="text" id="ann-tags" value="${this.escAttr(v.tags || "")}" placeholder="openmuscle, journal, demo, ..." onblur="app.saveField('tags', this.value)">
        <label style="margin-top:10px">Notes</label>
        <textarea id="ann-notes" placeholder="Why mine / why skip / what to look for..." onblur="app.saveField('notes', this.value)">${this.escape(v.notes || "")}</textarea>
      </div>
    `;
  },

  // ── Annotation save ──────────────────────────────────────────
  async saveField(field, value) {
    if (!this.currentId) return;
    await fetch(`/api/videos/${this.currentId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: value }),
    });
    // Field saves don't refresh the whole list — too noisy. The
    // sidebar will pick up changes on next loadVideos() (filter or
    // re-import).
  },

  async setDecision(decision) {
    if (!this.currentId) return;
    await fetch(`/api/videos/${this.currentId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    // Update local state + UI
    const v = this.videos.find((x) => x.id === this.currentId);
    if (v) v.decision = decision;
    document.querySelectorAll(".decision-btn").forEach((b) => b.classList.remove("active", "mine", "skip", "unsure", "pending"));
    const map = { mine: 0, skip: 1, unsure: 2, pending: 3 };
    const btns = document.querySelectorAll(".decision-btn");
    if (btns[map[decision]]) btns[map[decision]].classList.add("active", decision);
    this.renderList(); // refresh sidebar badges
    await this.loadStats(); // refresh header counts
    this.toast(`→ ${decision}`);
  },

  // ── Buttons ──────────────────────────────────────────────────
  async openInExplorer(id) {
    await fetch(`/api/videos/${id}/open`, { method: "POST" });
    this.toast("Opened in Explorer");
  },
  async playInDefaultApp(id) {
    await fetch(`/api/videos/${id}/play`, { method: "POST" });
    this.toast("Launched in default player");
  },
  async copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      this.toast("Copied: " + (text.length > 60 ? text.slice(0, 60) + "…" : text));
    } catch (e) {
      this.toast("Copy failed: " + e.message);
    }
  },
  async reimport() {
    this.toast("Re-importing…");
    const r = await fetch("/api/import", { method: "POST" });
    if (r.ok) {
      this.toast("Imported. Reloading list…");
      await this.loadStats();
      await this.loadVideos();
    } else {
      const err = await r.json();
      this.toast("Import failed: " + (err.error || "?"));
    }
  },

  // ── Navigation ───────────────────────────────────────────────
  prevVideo() {
    if (this.currentIndex > 0)
      this.selectVideo(this.videos[this.currentIndex - 1].id, this.currentIndex - 1);
  },
  nextVideo() {
    if (this.currentIndex >= 0 && this.currentIndex < this.videos.length - 1)
      this.selectVideo(this.videos[this.currentIndex + 1].id, this.currentIndex + 1);
  },

  setupKeyboard() {
    document.addEventListener("keydown", (e) => {
      // Skip when focus is in a text input — let typing flow
      const t = e.target.tagName;
      if (t === "INPUT" || t === "TEXTAREA" || t === "SELECT") {
        if (e.key === "Escape") e.target.blur();
        return;
      }
      if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); this.nextVideo(); }
      else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); this.prevVideo(); }
      else if (e.key === "m" || e.key === "M") { this.setDecision("mine"); }
      else if (e.key === "s" || e.key === "S") { this.setDecision("skip"); }
      else if (e.key === "u" || e.key === "U") { this.setDecision("unsure"); }
      else if (e.key === "p" || e.key === "P") { this.setDecision("pending"); }
      else if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        this.saveAll();
      }
    });
  },

  async saveAll() {
    if (!this.currentId) return;
    const data = {};
    for (const k of ["title", "description", "tags", "notes"]) {
      const el = document.getElementById("ann-" + k);
      if (el) data[k] = el.value;
    }
    await fetch(`/api/videos/${this.currentId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    this.toast("Saved");
  },

  // ── UI helpers ───────────────────────────────────────────────
  toast(msg) {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => el.classList.remove("show"), 1500);
  },

  fmtSize(b) {
    if (!b) return "—";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return b.toFixed(1) + " " + u[i];
  },
  fmtDur(s) {
    if (!s) return "—";
    s = Math.floor(s);
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m" + String(s % 60).padStart(2, "0") + "s";
    return Math.floor(s / 3600) + "h" + String(Math.floor((s % 3600) / 60)).padStart(2, "0") + "m";
  },
  escape(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  },
  escAttr(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[\\'"]/g, (c) => "\\" + c).replace(/\n/g, "\\n");
  },
};

document.addEventListener("DOMContentLoaded", () => app.init());
