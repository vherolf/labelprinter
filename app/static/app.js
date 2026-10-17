const $ = (sel) => document.querySelector(sel);

function toast(message, ok = true) {
  const el = $("#toast");
  el.textContent = message;
  el.className = ok ? "ok" : "err";
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), ok ? 3500 : 7000);
}

async function api(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => `${d.loc.at(-1)}: ${d.msg}`).join(", ")
      : data.detail;
    throw new Error(detail || res.statusText);
  }
  return data;
}

function readForm(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.value === "") {
      if ("nullable" in el.dataset) out[el.name] = null;
    } else if (el.type === "number" || "nullable" in el.dataset) out[el.name] = Number(el.value);
    else out[el.name] = el.value;
  }
  return out;
}

function fillForm(form, params) {
  for (const [name, value] of Object.entries(params || {})) {
    const el = form.elements[name];
    if (!el) continue;
    if (el.type === "checkbox") el.checked = !!value;
    else el.value = value ?? "";
  }
  if ((params?.counter_count ?? 1) > 1 || params?.rows?.length) form.querySelector("details.numbering").open = true;
}

// Batch rows from a CSV / Excel file; {Column} placeholders are filled per row.
function initBatch(form, params, onChange) {
  const state = { name: "", rows: [], row: 0 };
  let lastField = form.querySelector("textarea, input[type=text], input:not([type])");
  form.addEventListener("focusin", (ev) => {
    if (ev.target.matches("textarea, input[type=text], input:not([type])")) lastField = ev.target;
  });

  const show = () => {
    const on = state.rows.length > 0;
    $("#batch-info").hidden = !on;
    $("#row-nav").hidden = !on;
    form.elements.counter_count.disabled = on;
    if (!on) return;
    $("#batch-name").textContent = state.name;
    $("#batch-count").textContent = `${state.rows.length} row${state.rows.length === 1 ? "" : "s"}`;
    $("#row-label").textContent = `Row ${state.row + 1} of ${state.rows.length}`;
    $("#row-prev").disabled = state.row === 0;
    $("#row-next").disabled = state.row === state.rows.length - 1;
    $("#batch-columns").replaceChildren(...Object.keys(state.rows[0]).map((col) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn";
      b.textContent = `{${col}}`;
      b.title = `e.g. "${state.rows[0][col]}"`;
      return b;
    }));
  };
  const set = (name, rows) => {
    Object.assign(state, { name, rows, row: 0 });
    show();
    onChange();
  };

  $("#batch-columns").addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b || !lastField) return;
    const { selectionStart: a = lastField.value.length, selectionEnd: z = a } = lastField;
    lastField.value = lastField.value.slice(0, a) + b.textContent + lastField.value.slice(z);
    lastField.focus();
    lastField.setSelectionRange(a + b.textContent.length, a + b.textContent.length);
    onChange();
  });
  $("#batch-file").addEventListener("change", async (ev) => {
    const file = ev.target.files[0];
    ev.target.value = "";
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    try {
      const res = await fetch("/api/table", { method: "POST", body });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || res.statusText);
      set(data.name, data.rows);
      toast(`${data.rows.length} rows loaded from ${data.name}`);
    } catch (e) {
      toast(e.message, false);
    }
  });
  $("#batch-clear").addEventListener("click", () => set("", []));
  const step = (d) => { state.row = Math.max(0, Math.min(state.rows.length - 1, state.row + d)); show(); onChange(); };
  $("#row-prev").addEventListener("click", () => step(-1));
  $("#row-next").addEventListener("click", () => step(1));

  if (params?.rows?.length) Object.assign(state, { name: params.rows_name || "", rows: params.rows });
  show();
  return () => ({ rows: state.rows, rows_name: state.name, preview_row: state.row });
}

function initEditor(params) {
  const form = $("#label-form");
  const printer = form.dataset.printer;
  fillForm(form, params);
  const job = () => ({ ...readForm(form), ...batch() });

  let seq = 0;
  const preview = async () => {
    const mine = ++seq;
    try {
      const data = await api(`/api/${printer}/preview`, job());
      if (mine !== seq) return;
      $("#preview").src = data.image;
      const info = [];
      if (data.tape_mm) info.push(`${data.tape_mm === 4 ? 3.5 : data.tape_mm} mm tape`);
      if (data.length_mm) info.push(`${data.length_mm} mm long`);
      info.push(data.count === 1 ? "1 label" : `${data.count} labels`);
      $("#preview-info").textContent = info.join(" · ");
      $("#warnings").innerHTML = "";
      for (const w of data.warnings) {
        const li = document.createElement("li");
        li.textContent = w;
        $("#warnings").append(li);
      }
    } catch (e) {
      if (mine === seq) $("#preview-info").textContent = "Preview failed: " + e.message;
    }
  };
  let timer;
  const schedule = () => { clearTimeout(timer); timer = setTimeout(preview, 200); };
  form.addEventListener("input", schedule);
  form.addEventListener("change", schedule);
  window.refreshPreview = preview;
  const batch = initBatch(form, params, preview);
  preview();

  const btn = $("#print-btn");
  const print = async (ev) => {
    ev?.preventDefault();
    if (btn.disabled) return;
    btn.disabled = true;
    btn.classList.add("busy");
    try {
      const data = await api(`/api/${printer}/print`, job());
      toast(data.message);
    } catch (e) {
      toast(e.message, false);
    } finally {
      btn.disabled = false;
      btn.classList.remove("busy");
    }
  };
  form.addEventListener("submit", print);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) print(ev);
  });
}

function setChip(id, st, text) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("ok", !!st.ok);
  el.classList.toggle("bad", !st.ok);
}

function brotherText(st) {
  if (!st.tape_mm) return `Brother: ${st.message}`;
  const tape = st.tape_mm === 4 ? 3.5 : st.tape_mm;
  const extra = st.errors.length ? ` · ${st.message}` : "";
  return `${tape} mm ${st.media} · ${st.text_color} on ${st.tape_color}${extra}`;
}

async function pollStatus() {
  try {
    const res = await fetch("/api/status");
    const st = await res.json();
    setChip("zebra-status", st.zebra, st.zebra.ok ? "ready" : st.zebra.message);
    setChip("brother-status", st.brother, brotherText(st.brother));
  } catch {}
  setTimeout(pollStatus, 5000);
}

function watchBrother() {
  let lastKey = null;
  const poll = async () => {
    try {
      const st = await (await fetch("/api/status")).json();
      const b = st.brother;
      setChip("brother-status", b, "Brother PT-P710BT · " + brotherText(b));
      const key = `${b.tape_mm}-${b.tape_color}-${b.text_color}`;
      if (lastKey !== null && key !== lastKey) window.refreshPreview?.();
      lastKey = key;
    } catch {}
    setTimeout(poll, 4000);
  };
  poll();
}

document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("[data-reprint]");
  if (!btn) return;
  btn.disabled = true;
  try {
    toast((await api(`/api/history/${btn.dataset.reprint}/print`)).message);
  } catch (e) {
    toast(e.message, false);
  } finally {
    btn.disabled = false;
  }
});
