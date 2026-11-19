/* Defect Management — UI helpers (ไม่พึ่ง framework) */
(function () {
  "use strict";
  const DM = (window.DM = window.DM || {});
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const mobile = window.matchMedia("(max-width: 1023px)").matches;

  // ---------- ไอคอน ----------
  DM.icons = () => window.lucide && window.lucide.createIcons();

  // ---------- การ์ดค่อย ๆ ปรากฏ ----------
  function reveal() {
    const els = document.querySelectorAll(".reveal");
    if (reduce || !("IntersectionObserver" in window)) {
      els.forEach((e) => e.classList.add("in"));
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) {
          const el = en.target;
          setTimeout(() => el.classList.add("in"), +(el.dataset.delay || 0));
          io.unobserve(el);
        }
      });
    }, { threshold: 0.08 });
    els.forEach((e) => io.observe(e));
  }

  // ---------- ตัวเลขนับขึ้น ----------
  function countUp() {
    document.querySelectorAll("[data-count]").forEach((el) => {
      const target = parseFloat(el.dataset.count) || 0;
      const dec = (el.dataset.count.split(".")[1] || "").length;
      if (reduce || target === 0) { el.textContent = target.toFixed(dec); return; }
      const dur = 1100, t0 = performance.now();
      const step = (t) => {
        const p = Math.min((t - t0) / dur, 1);
        const e = 1 - Math.pow(1 - p, 4);
        el.textContent = (target * e).toFixed(dec);
        if (p < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
  }

  // ---------- ดูรูปขยาย ----------
  function lightbox() {
    const dlg = document.getElementById("lightbox");
    if (!dlg) return;
    const img = dlg.querySelector("img");
    document.addEventListener("click", (e) => {
      const t = e.target.closest("[data-full]");
      if (t) { e.preventDefault(); img.src = t.dataset.full; dlg.showModal(); }
    });
    dlg.addEventListener("click", () => dlg.close());
  }

  // ---------- ย่อรูปก่อนอัปโหลด (ประหยัดเน็ตมือถือ) ----------
  function compress(file, maxSide = 1800, quality = 0.85) {
    return new Promise((resolve) => {
      if (!file.type.startsWith("image/") || file.type === "image/gif" || file.size < 900 * 1024) return resolve(file);
      const url = URL.createObjectURL(file);
      const im = new Image();
      im.onload = () => {
        let { width: w, height: h } = im;
        const s = Math.min(1, maxSide / Math.max(w, h));
        w = Math.round(w * s); h = Math.round(h * s);
        const c = document.createElement("canvas");
        c.width = w; c.height = h;
        c.getContext("2d").drawImage(im, 0, 0, w, h);
        URL.revokeObjectURL(url);
        c.toBlob((b) => {
          if (!b || b.size >= file.size) return resolve(file);
          resolve(new File([b], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" }));
        }, "image/jpeg", quality);
      };
      im.onerror = () => { URL.revokeObjectURL(url); resolve(file); };
      im.src = url;
    });
  }

  // ---------- ตัวเลือกรูป: ถ่ายรูป / เลือกจากคลัง / ลบ / พรีวิว ----------
  DM.picker = function (root) {
    const grid = root.querySelector("[data-picker-grid]");
    const target = root.querySelector("input[data-picker-target]");
    const sources = root.querySelectorAll("input[data-picker-source]");
    const counter = root.querySelector("[data-picker-count]");
    let files = [];
    let canDT = true;
    try { new DataTransfer(); } catch (e) { canDT = false; }
    if (!canDT) return; // เบราว์เซอร์เก่า: ปล่อยให้ input ส่งไฟล์ตรง ๆ

    sources.forEach((s) => s.removeAttribute("name"));
    target.setAttribute("name", "photos");

    function sync() {
      const dt = new DataTransfer();
      files.forEach((f) => dt.items.add(f));
      target.files = dt.files;
      if (counter) counter.textContent = files.length;
      root.dispatchEvent(new CustomEvent("picker:change", { detail: files.length, bubbles: true }));
    }
    function render() {
      grid.querySelectorAll(".picker-tile").forEach((t) => t.remove());
      const add = grid.querySelector(".picker-add-wrap");
      files.forEach((f, i) => {
        const tile = document.createElement("div");
        tile.className = "picker-tile";
        const url = URL.createObjectURL(f);
        tile.innerHTML = `<img alt="" src="${url}"><button type="button" aria-label="ลบรูป">✕</button>`;
        tile.querySelector("button").onclick = () => { files.splice(i, 1); render(); sync(); };
        grid.insertBefore(tile, add);
      });
    }
    sources.forEach((s) => s.addEventListener("change", async () => {
      const picked = Array.from(s.files || []);
      s.value = "";
      for (const f of picked) files.push(await compress(f));
      render(); sync();
    }));
    root.closest("form").addEventListener("submit", () => sync());
  };

  // ---------- ฟอร์ม 3 ขั้น (ข้อมูล → รูปภาพ → ตรวจสอบ) ----------
  DM.stepper = function (form) {
    const panes = form.querySelectorAll("[data-step]");
    const marks = form.querySelectorAll(".stepper .st");
    const bars = form.querySelectorAll(".stepper .bar");
    let cur = 0;
    function show(i) {
      cur = i;
      panes.forEach((p, k) => p.classList.toggle("hidden", k !== i));
      marks.forEach((m, k) => m.classList.toggle("on", k <= i));
      bars.forEach((b, k) => b.classList.toggle("on", k < i));
      if (i === panes.length - 1) review();
      window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
    }
    function valid(i) {
      const fields = panes[i].querySelectorAll("input, select, textarea");
      for (const f of fields) {
        if (!f.checkValidity()) { f.reportValidity(); return false; }
      }
      if (panes[i].querySelector("[data-picker]")) {
        const n = form.querySelector("input[data-picker-target]").files.length;
        const old = +(form.dataset.existingPhotos || 0);
        if (n + old === 0) { alertBox("กรุณาแนบรูปภาพอย่างน้อย 1 รูป"); return false; }
      }
      return true;
    }
    function alertBox(msg) {
      const box = form.querySelector("[data-step-error]");
      box.textContent = msg; box.classList.remove("hidden");
      setTimeout(() => box.classList.add("hidden"), 3500);
    }
    function review() {
      const out = form.querySelector("[data-review]");
      if (!out) return;
      const val = (n) => form.elements[n];
      const txt = (sel) => (sel && sel.selectedOptions && sel.selectedOptions[0] ? sel.selectedOptions[0].text : "");
      const sev = form.querySelector("input[name=severity]:checked");
      const due = val("due_date").value;
      const rows = [
        ["พื้นที่ / หน่วยงาน", txt(val("area_id"))],
        ["ตำแหน่ง / ชั้น", val("location").value],
        ["รายละเอียดปัญหา", val("detail").value],
        ["ความรุนแรง", sev ? sev.nextElementSibling.textContent.trim() : ""],
        ["กำหนดแก้ไข", due ? new Date(due + "T00:00").toLocaleDateString("th-TH", { day: "numeric", month: "short", year: "numeric" }) : ""],
        ["ผู้รับผิดชอบ", txt(val("assignee_id"))],
        ["รูปภาพ", form.querySelector("input[data-picker-target]").files.length + " รูป"],
      ];
      if (val("inspector_id") && val("inspector_id").tagName === "SELECT") rows.splice(5, 0, ["ผู้ตรวจ", txt(val("inspector_id"))]);
      out.innerHTML = rows.map(([k, v]) => `<div class="flex gap-3 py-2 border-b border-white/5"><div class="w-36 shrink-0 dim text-sm">${k}</div><div class="whitespace-pre-line">${(v || "-").replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]))}</div></div>`).join("");
    }
    form.querySelectorAll("[data-next]").forEach((b) => b.addEventListener("click", () => valid(cur) && show(cur + 1)));
    form.querySelectorAll("[data-prev]").forEach((b) => b.addEventListener("click", () => show(cur - 1)));
    marks.forEach((m, k) => m.addEventListener("click", () => { if (k < cur) show(k); }));
    form.addEventListener("submit", (e) => {
      for (let i = 0; i < panes.length - 1; i++) if (!valid(i)) { e.preventDefault(); show(i); return; }
      const btn = form.querySelector("[type=submit]");
      btn.disabled = true; btn.innerHTML = "กำลังบันทึก…";
    });
    show(+(form.dataset.startStep || 0));
  };

  // ---------- กราฟ (ApexCharts) ----------
  const FONT = '"IBM Plex Sans Thai", sans-serif';
  DM.colors = { ok: "#059669", warn: "#d97706", bad: "#dc2626", blue: "#1d4ed8", violet: "#6d28d9", cyan: "#0891b2", orange: "#ea580c" };
  DM.chartBase = function () {
    return {
      chart: { fontFamily: FONT, foreColor: "#475569", background: "transparent", toolbar: { show: false },
        animations: { enabled: !reduce, speed: 900 }, parentHeightOffset: 0 },
      theme: { mode: "light" },
      grid: { borderColor: "#e2e8f0", strokeDashArray: 4 },
      dataLabels: { enabled: false },
      legend: { labels: { colors: "#475569" }, fontSize: "13px" },
      tooltip: { theme: "light" },
    };
  };
  DM.chart = function (el, opts) {
    if (!window.ApexCharts || !el) return null;
    const base = DM.chartBase();
    const cfg = Object.assign({}, base, opts, { chart: Object.assign({}, base.chart, opts.chart || {}) });
    const c = new ApexCharts(el, cfg);
    c.render();
    return c;
  };

  // ---------- เริ่มทำงาน ----------
  document.addEventListener("DOMContentLoaded", () => {
    DM.icons();
    reveal();
    countUp();
    lightbox();
    document.querySelectorAll("[data-picker]").forEach((p) => DM.picker(p));
    document.querySelectorAll("form[data-stepper]").forEach((f) => DM.stepper(f));
    document.querySelectorAll("form[data-confirm]").forEach((f) =>
      f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); }));
    document.querySelectorAll("[data-autosubmit]").forEach((el) =>
      el.addEventListener("change", () => el.form.submit()));
    // ปุ่ม "ให้ AI ตรวจรูป": ส่งผ่าน fetch แล้วแทนที่เฉพาะการ์ด (ไม่มี JS ก็ยังส่งฟอร์มปกติได้)
    document.addEventListener("submit", async (e) => {
      const f = e.target.closest("form[data-ai-check]");
      if (!f) return;
      e.preventDefault();
      const b = f.querySelector("[type=submit]");
      if (b) { b.disabled = true; b.textContent = f.dataset.busyText; }
      try {
        const r = await fetch(f.action, { method: "POST", body: new FormData(f), headers: { "X-Requested-With": "fetch" } });
        if (!r.ok) throw new Error(r.status);
        const card = document.getElementById("ai");
        card.outerHTML = await r.text();
        const fresh = document.getElementById("ai");
        if (fresh) fresh.classList.add("in"); // การ์ดใหม่ไม่ผ่าน reveal() ตอนโหลดหน้า
        DM.icons();
      } catch (err) { window.location.reload(); }
    });
    document.querySelectorAll("form[data-busy]").forEach((f) => f.addEventListener("submit", () => {
      const b = f.querySelector("[type=submit]"); if (b) { b.disabled = true; b.textContent = f.dataset.busy; }
    }));
    setTimeout(() => document.querySelectorAll("[data-flash]").forEach((el) => {
      el.style.transition = "opacity .6s"; el.style.opacity = "0"; setTimeout(() => el.remove(), 700);
    }), 6000);
  });
  DM.mobile = mobile;
})();
