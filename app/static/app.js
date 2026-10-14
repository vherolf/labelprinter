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
  if ((params?.counter_count ?? 1) > 1) form.querySelector("details.numbering").open = true;
}

function initEditor(params) {
  const form = $("#label-form");
  const printer = form.dataset.printer;
  fillForm(form, params);

  let seq = 0;
  const preview = async () => {
    const mine = ++seq;
    try {
      const data = await api(`/api/${printer}/preview`, readForm(form));
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
  preview();

  const btn = $("#print-btn");
  const print = async (ev) => {
    ev?.preventDefault();
    if (btn.disabled) return;
    btn.disabled = true;
    btn.classList.add("busy");
    try {
      const data = await api(`/api/${printer}/print`, readForm(form));
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
