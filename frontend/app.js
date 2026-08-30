"use strict";

// ---------- Tab switching ----------
const tabSingle = document.getElementById("tab-single");
const tabBatch = document.getElementById("tab-batch");
const panelSingle = document.getElementById("panel-single");
const panelBatch = document.getElementById("panel-batch");

function showTab(which) {
  const single = which === "single";
  tabSingle.classList.toggle("active", single);
  tabBatch.classList.toggle("active", !single);
  tabSingle.setAttribute("aria-selected", String(single));
  tabBatch.setAttribute("aria-selected", String(!single));
  panelSingle.classList.toggle("hidden", !single);
  panelBatch.classList.toggle("hidden", single);
}
tabSingle.addEventListener("click", () => showTab("single"));
tabBatch.addEventListener("click", () => showTab("batch"));

// ---------- Sign out ----------
const signoutBtn = document.getElementById("signout-btn");
if (signoutBtn) {
  signoutBtn.addEventListener("click", async () => {
    try {
      await fetch("/api/logout", { method: "POST" });
    } catch {
      /* ignore */
    }
    window.location.href = "/login";
  });
}

// ---------- Icons (status -> inline SVG, colored via CSS) ----------
const SVG = {
  check:
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.4 2.4 4.6-5"/></svg>',
  cross:
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/></svg>',
  alert:
    '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/></svg>',
};
const STATUS_SVG = {
  pass: SVG.check,
  match: SVG.check,
  fail: SVG.cross,
  mismatch: SVG.cross,
  needs_review: SVG.alert,
  missing: SVG.alert,
  error: SVG.alert,
};

// Builds a colored icon span. SVG strings are constant/trusted (never user data).
function iconSpan(status) {
  const s = document.createElement("span");
  s.className = "icon " + status;
  s.innerHTML = STATUS_SVG[status] || SVG.alert;
  return s;
}

// The application field keys sent with every single-label verification.
const FIELD_IDS = [
  "brand_name",
  "beverage_type",
  "class_type",
  "alcohol_content",
  "net_contents",
  "producer_name_address",
  "country_of_origin",
];

// ---------- Single: image picker ----------
const dropzone = document.getElementById("dropzone");
const imageInput = document.getElementById("image-input");
const preview = document.getElementById("preview");
const dropzoneText = document.getElementById("dropzone-text");

dropzone.addEventListener("click", () => imageInput.click());
dropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    imageInput.click();
  }
});
["dragover", "dragenter"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) {
    imageInput.files = e.dataTransfer.files;
    showPreview(file);
  }
});
imageInput.addEventListener("change", () => {
  if (imageInput.files[0]) showPreview(imageInput.files[0]);
});

let previewUrl = null;
function showPreview(file) {
  if (previewUrl) URL.revokeObjectURL(previewUrl); // avoid leaking object URLs
  previewUrl = URL.createObjectURL(file);
  preview.src = previewUrl;
  preview.classList.remove("hidden");
  dropzoneText.classList.add("hidden");
}

// ---------- Single: submit ----------
const singleForm = document.getElementById("single-form");
const verifyBtn = document.getElementById("verify-btn");
const singleStatus = document.getElementById("single-status");
const singleResult = document.getElementById("single-result");

singleForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!imageInput.files[0]) {
    setStatus(singleStatus, "Please choose a label image first.", true);
    return;
  }
  const fd = new FormData();
  fd.append("image", imageInput.files[0]);
  for (const id of FIELD_IDS) fd.append(id, document.getElementById(id).value);

  verifyBtn.disabled = true;
  verifyBtn.textContent = "Checking…";
  singleResult.classList.add("hidden");
  setStatus(singleStatus, "Reading the label… this takes a few seconds.");

  try {
    const res = await fetch("/api/verify", { method: "POST", body: fd });
    const data = await parseJsonSafe(res);
    if (!res.ok) throw new Error(detailOf(data) || "Verification failed.");
    singleStatus.classList.add("hidden");
    renderResult(singleResult, data);
    singleResult.classList.remove("hidden");
    singleResult.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    setStatus(singleStatus, err.message, true);
  } finally {
    verifyBtn.disabled = false;
    verifyBtn.textContent = "Verify label";
  }
});

async function parseJsonSafe(res) {
  try {
    return await res.json();
  } catch {
    return {};
  }
}
function detailOf(data) {
  if (!data) return "";
  if (typeof data.detail === "string") return data.detail;
  return "";
}

function setStatus(el, msg, isError = false) {
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.classList.remove("hidden");
}

// ---------- Render a single result ----------
function renderResult(container, data) {
  container.innerHTML = "";

  const verdictLabel = {
    pass: "PASS — label matches",
    fail: "FAIL — problems found",
    needs_review: "NEEDS REVIEW",
  }[data.overall_status];

  const verdict = document.createElement("div");
  verdict.className = `verdict ${data.overall_status}`;
  verdict.appendChild(iconSpan(data.overall_status));
  verdict.appendChild(makeSpan("", verdictLabel));
  container.appendChild(verdict);

  const summary = document.createElement("p");
  summary.className = "verdict-summary";
  summary.textContent = data.summary || "";
  container.appendChild(summary);

  (data.field_checks || []).forEach((f) => {
    container.appendChild(
      checkRow(f.status, f.field_name, [
        ["Application", f.expected_value || "—"],
        ["On label", f.found_on_label || "—"],
      ], f.explanation)
    );
  });

  // Government warning (the strict, detailed check)
  const gw = data.government_warning;
  const row = document.createElement("div");
  row.className = "check-row";
  row.appendChild(iconSpan(gw.status));

  const body = document.createElement("div");
  const name = document.createElement("div");
  name.className = "check-name";
  name.append("Government Health Warning ");
  name.appendChild(pill(gw.status));
  body.appendChild(name);

  const block = document.createElement("div");
  block.className = "warning-block";
  block.appendChild(flags([
    ["Present", gw.present],
    ["Header all-caps", gw.header_all_caps],
    ["Header bold", gw.header_bold],
    ["Text exact", gw.text_matches_exactly],
    ["Legible size", gw.legible],
  ]));
  if (gw.issues && gw.issues.length) {
    const ul = document.createElement("ul");
    gw.issues.forEach((i) => {
      const li = document.createElement("li");
      li.textContent = i;
      ul.appendChild(li);
    });
    block.appendChild(ul);
  }
  if (gw.found_text) {
    const ft = document.createElement("div");
    ft.className = "warning-found";
    ft.textContent = gw.found_text;
    block.appendChild(ft);
  }
  body.appendChild(block);
  row.appendChild(body);
  container.appendChild(row);

  if (!data.image_quality_ok && data.image_quality_note) {
    const q = document.createElement("div");
    q.className = "quality-note";
    q.textContent = "Image quality: " + data.image_quality_note;
    container.appendChild(q);
  }
}

// ---------- Small DOM builders (no innerHTML with data -> no XSS) ----------
function makeSpan(cls, text) {
  const s = document.createElement("span");
  if (cls) s.className = cls;
  s.textContent = text;
  return s;
}
function pill(status) {
  const map = { match: "match", mismatch: "mismatch", missing: "missing", pass: "pass", fail: "fail" };
  const p = document.createElement("span");
  p.className = "pill " + (map[status] || "review");
  p.textContent = status;
  return p;
}
function checkRow(status, fieldName, rows, explanation) {
  const row = document.createElement("div");
  row.className = "check-row";
  row.appendChild(iconSpan(status));
  const body = document.createElement("div");
  const name = document.createElement("div");
  name.className = "check-name";
  name.append(fieldName + " ");
  name.appendChild(pill(status));
  body.appendChild(name);
  const detail = document.createElement("div");
  detail.className = "check-detail";
  rows.forEach(([k, v]) => {
    detail.append(k + ": ");
    const b = document.createElement("b");
    b.textContent = v;
    detail.appendChild(b);
    detail.appendChild(document.createElement("br"));
  });
  detail.append(explanation || "");
  body.appendChild(detail);
  row.appendChild(body);
  return row;
}
function flags(pairs) {
  const wrap = document.createElement("div");
  pairs.forEach(([label, ok], idx) => {
    if (idx) wrap.append("  |  ");
    wrap.append(label + ": ");
    const b = document.createElement("b");
    b.textContent = ok ? "yes" : "no";
    wrap.appendChild(b);
  });
  return wrap;
}

// ---------- Batch ----------
const batchForm = document.getElementById("batch-form");
const batchBtn = document.getElementById("batch-btn");
const batchStatus = document.getElementById("batch-status");
const batchSummary = document.getElementById("batch-summary");
const batchResults = document.getElementById("batch-results");

batchForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const csv = document.getElementById("csv-input").files[0];
  const images = document.getElementById("batch-images-input").files;
  if (!csv || !images.length) {
    setStatus(batchStatus, "Please choose a CSV and at least one image.", true);
    return;
  }

  const fd = new FormData();
  fd.append("csv_file", csv);
  for (const img of images) fd.append("images", img);

  batchBtn.disabled = true;
  batchBtn.textContent = "Checking…";
  batchSummary.classList.add("hidden");
  batchResults.innerHTML = "";
  setStatus(batchStatus, `Checking ${images.length} labels… this may take a moment.`);

  try {
    const res = await fetch("/api/verify-batch", { method: "POST", body: fd });
    const data = await parseJsonSafe(res);
    if (!res.ok) throw new Error(detailOf(data) || "Batch verification failed.");
    batchStatus.classList.add("hidden");
    renderBatch(data);
  } catch (err) {
    setStatus(batchStatus, err.message, true);
  } finally {
    batchBtn.disabled = false;
    batchBtn.textContent = "Verify all labels";
  }
});

function renderBatch(data) {
  batchSummary.innerHTML = "";
  [
    ["total", data.total, "Total"],
    ["pass", data.passed, "Passed"],
    ["fail", data.failed, "Failed"],
    ["review", data.needs_review, "Review"],
    ["error", data.errored, "Errors"],
  ].forEach(([cls, num, label]) => {
    const chip = document.createElement("div");
    chip.className = "summary-chip " + cls;
    const n = document.createElement("span");
    n.className = "num";
    n.textContent = num;
    chip.appendChild(n);
    chip.append(label);
    batchSummary.appendChild(chip);
  });
  batchSummary.classList.remove("hidden");

  // Failures and reviews float to the top — that's what an agent acts on.
  const order = { fail: 0, needs_review: 1, error: 2, pass: 3 };
  const rows = [...data.results].sort((a, b) => {
    const ka = a.error ? "error" : a.result.overall_status;
    const kb = b.error ? "error" : b.result.overall_status;
    return order[ka] - order[kb];
  });

  batchResults.innerHTML = "";
  rows.forEach((item) => {
    const row = document.createElement("div");
    row.className = "batch-row";
    if (item.error) {
      row.appendChild(iconSpan("error"));
      const mid = document.createElement("div");
      const fn = document.createElement("div");
      fn.className = "fname";
      fn.textContent = item.filename;
      const rs = document.createElement("div");
      rs.className = "rsummary";
      rs.textContent = "Error: " + item.error;
      mid.appendChild(fn);
      mid.appendChild(rs);
      row.appendChild(mid);
      row.appendChild(document.createElement("span"));
    } else {
      const r = item.result;
      row.appendChild(iconSpan(r.overall_status));
      const mid = document.createElement("div");
      const fn = document.createElement("div");
      fn.className = "fname";
      fn.textContent = item.filename;
      const rs = document.createElement("div");
      rs.className = "rsummary";
      rs.textContent = r.summary || "";
      mid.appendChild(fn);
      mid.appendChild(rs);
      row.appendChild(mid);
      const btn = document.createElement("button");
      btn.className = "details-btn";
      btn.type = "button";
      btn.textContent = "Details";
      btn.addEventListener("click", () => openModal(item.filename, r));
      row.appendChild(btn);
    }
    batchResults.appendChild(row);
  });
}

// ---------- Modal ----------
const modal = document.getElementById("modal");
const modalBody = document.getElementById("modal-body");
document.getElementById("modal-close").addEventListener("click", closeModal);
modal.addEventListener("click", (e) => {
  if (e.target === modal) closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !modal.classList.contains("hidden")) closeModal();
});

function openModal(filename, result) {
  modalBody.innerHTML = "";
  const h = document.createElement("h2");
  h.style.marginTop = "0";
  h.textContent = filename;
  modalBody.appendChild(h);
  const holder = document.createElement("div");
  renderResult(holder, result);
  modalBody.appendChild(holder);
  modal.classList.remove("hidden");
}
function closeModal() {
  modal.classList.add("hidden");
}
