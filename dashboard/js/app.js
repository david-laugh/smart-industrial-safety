// 대시보드 UI. 데이터는 PPE.createDataSource() 어댑터를 통해서만 읽는다(읽기 전용).
(function (PPE) {
  "use strict";

  const cfg = PPE.config;
  const $ = (id) => document.getElementById(id);

  const TYPE_LABEL = { HELMET_MISSING: "안전모 미착용", VEST_MISSING: "안전조끼 미착용" };
  const STATUS = {
    WORN: { cls: "worn", ico: "✓", label: "착용" },
    MISSING: { cls: "missing", ico: "✕", label: "미착용" },
    UNKNOWN: { cls: "unknown", ico: "?", label: "판정 불가" },
  };
  const SOURCE_LABEL = {
    DEVICE_SNTP: { text: "장치 시각(SNTP)", hint: "ESP32 촬영 시각을 사용" },
    GATEWAY_RECEIVED: { text: "게이트웨이 수신", hint: "장치 시각 미동기화로 Pi 수신 시각을 사용" },
  };

  const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const fmtFull = new Intl.DateTimeFormat("ko-KR", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  const fmtDay = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric" });
  const fmtHour = new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", hour12: false });
  const fmtNum = new Intl.NumberFormat("ko-KR");

  let ds = null;
  let unsubscribe = null;

  const state = {
    range: "7d",
    customFrom: "",
    customTo: "",
    deviceIds: [],
    types: [],
    page: 0,
    total: 0,
    rows: [],
    pendingNew: 0,
    freshIds: new Set(),
    selectedId: null,
    loadSeq: 0,
  };

  // ---------- 유틸 ----------

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // UTC로 저장된 시각을 표시할 때만 현지 시간대로 변환
  function localTime(isoStr) {
    return isoStr ? fmtFull.format(new Date(isoStr)) : "—";
  }

  function timeCell(isoStr) {
    return `<span class="time-main" title="UTC ${esc(isoStr)}">${esc(localTime(isoStr))}</span>`;
  }

  function typeTags(types) {
    return types.map((t) => `<span class="tag">${esc(TYPE_LABEL[t] || t)}</span>`).join("");
  }

  function sourceCell(src) {
    const s = SOURCE_LABEL[src] || { text: src, hint: "" };
    return `<span class="src ${src === "GATEWAY_RECEIVED" ? "fallback" : ""}" title="${esc(s.hint)}">${esc(s.text)}</span>`;
  }

  function startOfLocalDay(d) {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  }

  function dateInputValue(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  function parseDateInput(v) {
    const [y, m, d] = v.split("-").map(Number);
    return new Date(y, m - 1, d);
  }

  // ---------- 필터 ----------

  function currentFilter() {
    const now = new Date();
    let from = null;
    let to = null;
    if (state.range === "24h") from = new Date(now.getTime() - 24 * 3600 * 1000);
    else if (state.range === "7d") from = new Date(startOfLocalDay(now).getTime() - 6 * 86400000);
    else if (state.range === "30d") from = new Date(startOfLocalDay(now).getTime() - 29 * 86400000);
    else if (state.range === "custom") {
      if (state.customFrom) from = parseDateInput(state.customFrom);
      if (state.customTo) {
        const t = parseDateInput(state.customTo);
        to = new Date(t.getFullYear(), t.getMonth(), t.getDate() + 1);
      }
    }
    return {
      from: from ? from.toISOString() : null,
      to: to ? to.toISOString() : null,
      deviceIds: state.deviceIds.slice(),
      violationTypes: state.types.slice(),
    };
  }

  function matchesFilter(row, f) {
    if (f.from && row.occurred_at < f.from) return false;
    if (f.to && row.occurred_at >= f.to) return false;
    if (f.deviceIds.length && !f.deviceIds.includes(row.device_id)) return false;
    if (f.violationTypes.length && !row.violation_types.some((t) => f.violationTypes.includes(t))) return false;
    return true;
  }

  function renderRangeSeg() {
    for (const b of $("range-seg").querySelectorAll("button")) {
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(b.dataset.range === state.range));
    }
    $("custom-range").hidden = state.range !== "custom";
  }

  async function initDevices() {
    const devices = await ds.listDevices();
    $("device-options").innerHTML = devices
      .map((d) => `<label><input type="checkbox" value="${esc(d)}" ${state.deviceIds.includes(d) ? "checked" : ""}/> ${esc(d)}</label>`)
      .join("");
    state.allDevices = devices;
    renderDeviceSummary();
  }

  function renderDeviceSummary() {
    const n = state.deviceIds.length;
    $("device-summary").textContent = n === 0 ? "전체 장치" : n === 1 ? state.deviceIds[0] : `장치 ${n}개`;
  }

  function bindFilters() {
    $("range-seg").addEventListener("click", (e) => {
      const b = e.target.closest("button[data-range]");
      if (!b) return;
      state.range = b.dataset.range;
      if (state.range === "custom" && !state.customFrom) {
        const today = new Date();
        state.customTo = dateInputValue(today);
        state.customFrom = dateInputValue(new Date(today.getTime() - 13 * 86400000));
        $("custom-from").value = state.customFrom;
        $("custom-to").value = state.customTo;
      }
      renderRangeSeg();
      reload();
    });
    for (const id of ["custom-from", "custom-to"]) {
      $(id).addEventListener("change", () => {
        state.customFrom = $("custom-from").value;
        state.customTo = $("custom-to").value;
        reload();
      });
    }
    $("device-options").addEventListener("change", () => {
      state.deviceIds = Array.from($("device-options").querySelectorAll("input:checked")).map((i) => i.value);
      renderDeviceSummary();
      reload();
    });
    document.addEventListener("click", (e) => {
      const dd = $("device-dropdown");
      if (dd.open && !dd.contains(e.target)) dd.open = false;
    });
    $("type-chips").addEventListener("change", () => {
      state.types = Array.from($("type-chips").querySelectorAll("input:checked")).map((i) => i.value);
      reload();
    });
    $("reset-filters").addEventListener("click", () => {
      state.range = "7d";
      state.deviceIds = [];
      state.types = [];
      for (const i of document.querySelectorAll("#device-options input, #type-chips input")) i.checked = false;
      renderDeviceSummary();
      renderRangeSeg();
      reload();
    });
  }

  // ---------- 요약 / 차트 ----------

  function renderKpis(stats) {
    const helmet = stats.filter((r) => r.violation_types.includes("HELMET_MISSING")).length;
    const vest = stats.filter((r) => r.violation_types.includes("VEST_MISSING")).length;
    const people = stats.reduce((s, r) => s + r.violation_count, 0);
    const devices = new Set(stats.map((r) => r.device_id)).size;
    const total = (state.allDevices || []).length;
    const tiles = [
      { label: "이벤트", value: stats.length, sub: "선택한 조건" },
      { label: "안전모 미착용", value: helmet, sub: "포함 이벤트 수" },
      { label: "안전조끼 미착용", value: vest, sub: "포함 이벤트 수" },
      { label: "위반 인원", value: people, sub: "이벤트별 인원 합계" },
      { label: "이벤트 발생 장치", value: devices, sub: `전체 ${total}대 중` },
    ];
    $("kpis").innerHTML = tiles
      .map((t) => `<div class="kpi"><div class="kpi-label">${esc(t.label)}</div><div class="kpi-value">${fmtNum.format(t.value)}</div><div class="kpi-sub">${esc(t.sub)}</div></div>`)
      .join("");
  }

  function buildBuckets(f) {
    const now = new Date();
    if (state.range === "24h") {
      const end = new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours() + 1);
      const buckets = [];
      for (let i = 23; i >= 0; i--) {
        const s = new Date(end.getTime() - (i + 1) * 3600000);
        buckets.push({ start: s, end: new Date(s.getTime() + 3600000), label: fmtHour.format(s), long: localTime(s.toISOString()).slice(0, -3) + " 부터 1시간" });
      }
      return { unit: "hour", buckets };
    }
    const from = f.from ? startOfLocalDay(new Date(f.from)) : startOfLocalDay(new Date(now.getTime() - 29 * 86400000));
    const to = f.to ? new Date(f.to) : new Date(startOfLocalDay(now).getTime() + 86400000);
    const buckets = [];
    for (let d = from; d < to && buckets.length < 400; d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1)) {
      const e = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1);
      buckets.push({ start: d, end: e, label: fmtDay.format(d), long: new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium" }).format(d) });
    }
    return { unit: "day", buckets };
  }

  function niceMax(v) {
    if (v <= 4) return 4;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  function renderChart(stats, f) {
    const { unit, buckets } = buildBuckets(f);
    for (const b of buckets) b.count = 0;
    for (const r of stats) {
      const t = new Date(r.occurred_at);
      const b = buckets.find((x) => t >= x.start && t < x.end);
      if (b) b.count++;
    }
    $("chart-title").textContent = unit === "hour" ? "시간별 이벤트 수" : "일별 이벤트 수";
    $("chart-sub").textContent = `현지 시간대 ${TZ}`;

    const el = $("chart");
    const W = Math.max(280, el.clientWidth);
    const H = el.clientHeight || 200;
    const m = { l: 32, r: 8, t: 10, b: 24 };
    const iw = W - m.l - m.r;
    const ih = H - m.t - m.b;
    const max = niceMax(Math.max(1, ...buckets.map((b) => b.count)));
    const n = buckets.length || 1;
    const slot = iw / n;
    const gap = Math.min(2, slot * 0.2);
    const bw = Math.max(1, Math.min(28, slot - gap));
    const y = (v) => m.t + ih - (v / max) * ih;

    const ticks = [0, max / 2, max];
    const labelEvery = Math.ceil(n / Math.max(2, Math.floor(iw / 44)));

    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${unit === "hour" ? "시간별" : "일별"} 이벤트 수 막대 차트">`;
    for (const t of ticks) {
      svg += `<line class="${t === 0 ? "base" : "grid"}" x1="${m.l}" x2="${W - m.r}" y1="${y(t)}" y2="${y(t)}"/>`;
      svg += `<text class="tick" x="${m.l - 6}" y="${y(t) + 4}" text-anchor="end">${fmtNum.format(t)}</text>`;
    }
    buckets.forEach((b, i) => {
      const cx = m.l + slot * i + slot / 2;
      const x = cx - bw / 2;
      const h = (b.count / max) * ih;
      const r = Math.min(4, bw / 2, h);
      const top = m.t + ih - h;
      // 바닥은 직각, 위쪽 모서리만 둥글게
      const path = h > 0
        ? `M${x},${m.t + ih} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${m.t + ih} Z`
        : "";
      svg += `<rect class="hit" data-i="${i}" x="${m.l + slot * i}" y="${m.t}" width="${slot}" height="${ih}" fill="transparent"/>`;
      if (path) svg += `<path class="bar" d="${path}"/>`;
      if (i % labelEvery === 0) svg += `<text class="tick" x="${cx}" y="${H - 6}" text-anchor="middle">${esc(b.label)}</text>`;
    });
    svg += "</svg>";
    el.innerHTML = svg;

    const tip = $("chart-tip");
    el.onmousemove = (e) => {
      const hit = e.target.closest(".hit");
      if (!hit) return (tip.hidden = true);
      const b = buckets[Number(hit.dataset.i)];
      tip.innerHTML = `${esc(b.long)}<br><b>${fmtNum.format(b.count)}</b>건`;
      tip.hidden = false;
      const x = Math.min(window.innerWidth - tip.offsetWidth - 8, e.clientX + 12);
      tip.style.left = x + "px";
      tip.style.top = e.clientY - tip.offsetHeight - 10 + "px";
    };
    el.onmouseleave = () => (tip.hidden = true);
    state.lastChart = { stats, f };
  }

  // ---------- 목록 ----------

  function renderTable() {
    $("tz-label").textContent = `(${TZ})`;
    $("events-body").innerHTML = state.rows
      .map((r) => {
        const cls = [r.event_id === state.selectedId ? "selected" : "", state.freshIds.has(r.event_id) ? "fresh" : ""].join(" ");
        return `<tr data-id="${esc(r.event_id)}" class="${cls}" tabindex="0">
          <td>${timeCell(r.occurred_at)}</td>
          <td>${esc(r.device_id)}</td>
          <td>${typeTags(r.violation_types)}</td>
          <td class="num">${fmtNum.format(r.violation_count)}</td>
          <td>${sourceCell(r.occurred_at_source)}</td>
          <td class="muted">${esc(r.model_name)} ${esc(r.model_version)}</td>
        </tr>`;
      })
      .join("");
    $("list-empty").hidden = state.rows.length > 0;
    const pages = Math.max(1, Math.ceil(state.total / cfg.pageSize));
    $("page-info").textContent = `${state.page + 1} / ${pages}`;
    $("prev-page").disabled = state.page === 0;
    $("next-page").disabled = state.page + 1 >= pages;
    const start = state.total ? state.page * cfg.pageSize + 1 : 0;
    $("list-meta").textContent = `총 ${fmtNum.format(state.total)}건 · ${start}–${state.page * cfg.pageSize + state.rows.length} · 발생 시각 최신순`;
  }

  async function loadPage() {
    const seq = ++state.loadSeq;
    $("list-error").hidden = true;
    try {
      const res = await ds.listEvents(currentFilter(), { offset: state.page * cfg.pageSize, limit: cfg.pageSize });
      if (seq !== state.loadSeq) return;
      state.rows = res.rows;
      state.total = res.total;
      renderTable();
    } catch (err) {
      $("list-error").textContent = "이벤트를 불러오지 못했습니다: " + err.message;
      $("list-error").hidden = false;
    }
  }

  async function loadStats() {
    const f = currentFilter();
    try {
      const stats = await ds.listStats(f);
      renderKpis(stats);
      renderChart(stats, f);
    } catch (err) {
      $("kpis").innerHTML = `<p class="error">요약을 불러오지 못했습니다: ${esc(err.message)}</p>`;
    }
  }

  function reload() {
    state.page = 0;
    state.pendingNew = 0;
    $("new-events").hidden = true;
    loadPage();
    loadStats();
  }

  function bindList() {
    $("prev-page").addEventListener("click", () => {
      if (state.page > 0) {
        state.page--;
        loadPage();
      }
    });
    $("next-page").addEventListener("click", () => {
      state.page++;
      loadPage();
    });
    const open = (e) => {
      const tr = e.target.closest("tr[data-id]");
      if (tr) location.hash = "#event=" + tr.dataset.id;
    };
    $("events-body").addEventListener("click", open);
    $("events-body").addEventListener("keydown", (e) => {
      if (e.key === "Enter") open(e);
    });
    $("show-new").addEventListener("click", reload);
  }

  // ---------- 상세 ----------

  function statusBadge(item) {
    const s = STATUS[item.status] || STATUS.UNKNOWN;
    return `<span class="status ${s.cls}"><span class="ico" aria-hidden="true">${s.ico}</span>${s.label}</span>`;
  }

  function confBar(c) {
    if (c == null) return `<span class="conf">신뢰도 없음</span>`;
    const pct = Math.round(c * 100);
    return `<span class="conf"><span class="conf-track"><span class="conf-fill" style="width:${pct}%"></span></span>${pct}%</span>`;
  }

  function personLabel(v) {
    return v.violation_types.map((t) => TYPE_LABEL[t] || t).join(" · ");
  }

  function renderDetail(row) {
    const p = row.event_payload;
    const ts = p.timestamps;
    const src = SOURCE_LABEL[row.occurred_at_source] || { text: row.occurred_at_source, hint: "" };
    const ratio = `${p.image.width} / ${p.image.height}`;

    const boxes = p.violations
      .map((v) => {
        const b = v.bbox;
        const style = `left:${b.x_min * 100}%;top:${b.y_min * 100}%;width:${(b.x_max - b.x_min) * 100}%;height:${(b.y_max - b.y_min) * 100}%`;
        return `<div class="bbox ${b.y_min < 0.06 ? "flip" : ""}" style="${style}"><span class="bbox-label">P${v.person_index} · ${esc(personLabel(v))}</span></div>`;
      })
      .join("");

    const people = p.violations
      .map(
        (v) => `<div class="person">
          <div class="person-head"><span>사람 P${v.person_index}${v.track_id ? ` <span class="muted small mono">${esc(v.track_id)}</span>` : ""}</span><span>${typeTags(v.violation_types)}</span></div>
          <div class="ppe-row"><span>안전모</span>${statusBadge(v.helmet)}${confBar(v.helmet.confidence)}</div>
          <div class="ppe-row"><span>안전조끼</span>${statusBadge(v.vest)}${confBar(v.vest.confidence)}</div>
        </div>`
      )
      .join("");

    const kvTime = (label, isoStr) => `<dt>${esc(label)}</dt><dd>${isoStr ? `${esc(localTime(isoStr))} <span class="muted small mono">${esc(isoStr)}</span>` : "—"}</dd>`;

    $("detail-body").innerHTML = `
      <section>
        <div class="evidence" style="aspect-ratio:${ratio}">
          <img id="evidence-img" alt="증거 이미지 ${esc(row.event_id)}" hidden />
          <div class="evidence-msg" id="evidence-msg">이미지 불러오는 중…</div>
          ${boxes}
        </div>
        <div class="evidence-tools">
          <span class="muted small" id="signed-meta"></span>
          <span>
            <label class="small"><input type="checkbox" id="bbox-toggle" checked /> bbox 표시</label>
            <button type="button" class="btn ghost small" id="resign-btn">URL 재발급</button>
          </span>
        </div>
      </section>

      <section>
        <h3>위반 인원 ${p.violations.length}명</h3>
        <div class="people">${people}</div>
      </section>

      <section>
        <h3>시간</h3>
        <dl class="kv">
          ${kvTime("발생 시각", row.occurred_at)}
          <dt>시각 출처</dt><dd>${sourceCell(row.occurred_at_source)} <span class="muted small">${esc(src.hint)}</span></dd>
          ${kvTime("촬영(장치)", ts.capture_ts_utc)}
          ${kvTime("게이트웨이 수신", row.gateway_received_at)}
          ${kvTime("판정", row.decided_at)}
          ${kvTime("클라우드 적재", row.ingested_at)}
          <dt>게이트웨이 시계</dt><dd>${ts.gateway_clock_synced ? "동기화됨" : "미동기화"}</dd>
          <dt>MCU uptime</dt><dd>${fmtNum.format(ts.mcu_uptime_ms)} ms</dd>
        </dl>
      </section>

      <section>
        <h3>장치 · 모델 · 판정</h3>
        <dl class="kv">
          <dt>장치</dt><dd>${esc(row.device_id)}</dd>
          <dt>boot / frame</dt><dd class="mono">${esc(p.boot_id)} / #${esc(p.frame_seq)}</dd>
          <dt>모델</dt><dd>${esc(p.model.name)} ${esc(p.model.version)} <span class="muted small">(${esc(p.model.runtime)} ${esc(p.model.runtime_version)}, ${p.model.input_width}×${p.model.input_height})</span></dd>
          <dt>판정 정책</dt><dd>${esc(p.decision.policy_version)} · 프레임 ${p.decision.frames_considered}장 · 중복 억제 ${p.decision.dedup_window_s}s</dd>
          <dt>dedup_key</dt><dd class="mono">${esc(p.decision.dedup_key)}</dd>
        </dl>
      </section>

      <section>
        <h3>식별자 · 저장소</h3>
        <dl class="kv">
          <dt>event_id</dt><dd class="mono">${esc(row.event_id)}</dd>
          <dt>frame_id</dt><dd class="mono">${esc(p.frame_id)}</dd>
          <dt>Storage</dt><dd class="mono">${esc(row.storage_bucket)}/${esc(row.storage_path)}</dd>
          <dt>이미지</dt><dd>${p.image.width}×${p.image.height} · ${fmtNum.format(p.image.byte_length)} B</dd>
          <dt>SHA-256</dt><dd class="mono">${esc(row.image_sha256)}</dd>
          <dt>스키마</dt><dd>${esc(row.schema_version)}</dd>
        </dl>
      </section>

      <details class="raw"><summary>원본 event_payload (JSON)</summary><pre>${esc(JSON.stringify(p, null, 2))}</pre></details>
    `;

    $("bbox-toggle").addEventListener("change", (e) => {
      for (const b of $("detail-body").querySelectorAll(".bbox")) b.hidden = !e.target.checked;
    });
    $("resign-btn").addEventListener("click", () => loadEvidence(row));
    loadEvidence(row);
  }

  async function loadEvidence(row) {
    const img = $("evidence-img");
    const msg = $("evidence-msg");
    img.hidden = true;
    msg.hidden = false;
    msg.textContent = "Signed URL 발급 중…";
    try {
      const { url, expiresAt } = await ds.getSignedImageUrl(row);
      if (state.selectedId !== row.event_id) return;
      img.onload = () => {
        img.hidden = false;
        msg.hidden = true;
      };
      img.onerror = () => {
        msg.textContent = "이미지를 불러오지 못했습니다(만료되었을 수 있음). URL 재발급을 눌러 주세요.";
      };
      img.src = url;
      $("signed-meta").textContent = `Signed URL 만료 ${localTime(expiresAt)}`;
    } catch (err) {
      msg.textContent = "Signed URL 발급 실패: " + err.message;
    }
  }

  async function openFromHash() {
    const m = /^#event=([0-9a-f-]{36})$/.exec(location.hash);
    if (!m) return closeDetail(false);
    const id = m[1];
    state.selectedId = id;
    renderTable();
    $("detail").hidden = false;
    $("detail-backdrop").hidden = false;
    $("detail-body").innerHTML = `<p class="muted">불러오는 중…</p>`;
    const row = state.rows.find((r) => r.event_id === id) || (await ds.getEvent(id));
    if (state.selectedId !== id) return;
    if (!row) {
      $("detail-body").innerHTML = `<p class="error">이벤트를 찾을 수 없습니다: ${esc(id)}</p>`;
      return;
    }
    renderDetail(row);
  }

  function closeDetail(clearHash) {
    state.selectedId = null;
    $("detail").hidden = true;
    $("detail-backdrop").hidden = true;
    if (clearHash !== false && location.hash) history.replaceState(null, "", location.pathname + location.search);
    renderTable();
  }

  function bindDetail() {
    window.addEventListener("hashchange", openFromHash);
    $("detail-close").addEventListener("click", () => closeDetail());
    $("detail-backdrop").addEventListener("click", () => closeDetail());
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("detail").hidden) closeDetail();
    });
  }

  // ---------- 실시간 ----------

  function onLiveInsert(row) {
    if (!matchesFilter(row, currentFilter())) return;
    state.freshIds.add(row.event_id);
    state.pendingNew++;
    $("new-count").textContent = state.pendingNew;
    $("new-events").hidden = false;
  }

  function bindLive() {
    $("live-toggle").addEventListener("change", (e) => {
      if (e.target.checked) unsubscribe = ds.subscribe(onLiveInsert);
      else if (unsubscribe) {
        unsubscribe();
        unsubscribe = null;
      }
    });
  }

  // ---------- 인증 / 시작 ----------

  function showLogin() {
    $("app-view").hidden = true;
    $("login-view").hidden = false;
    $("login-mock-note").hidden = ds.kind !== "mock";
    $("login-password").required = ds.kind !== "mock";
    $("login-email").focus();
  }

  async function showApp(session) {
    $("login-view").hidden = true;
    $("app-view").hidden = false;
    $("user-email").textContent = (session.user && session.user.email) || "";
    const badge = $("source-badge");
    badge.textContent = ds.kind === "mock" ? "MOCK 데이터" : "Supabase";
    badge.classList.toggle("mock", ds.kind === "mock");
    renderRangeSeg();
    await initDevices();
    reload();
    if (location.hash) openFromHash();
  }

  function bindAuth() {
    $("login-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      $("login-error").hidden = true;
      try {
        const session = await ds.auth.signIn($("login-email").value.trim(), $("login-password").value);
        $("login-password").value = "";
        showApp(session);
      } catch (err) {
        $("login-error").textContent = "로그인 실패: " + err.message;
        $("login-error").hidden = false;
      }
    });
    $("logout-btn").addEventListener("click", async () => {
      if (unsubscribe) unsubscribe();
      unsubscribe = null;
      $("live-toggle").checked = false;
      closeDetail();
      await ds.auth.signOut();
      showLogin();
    });
  }

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (state.lastChart) renderChart(state.lastChart.stats, state.lastChart.f);
    }, 150);
  });

  async function main() {
    try {
      ds = await PPE.createDataSource();
    } catch (err) {
      document.body.innerHTML = `<p class="error" style="padding:24px">데이터 소스 초기화 실패: ${esc(err.message)}</p>`;
      return;
    }
    bindAuth();
    bindFilters();
    bindList();
    bindDetail();
    bindLive();
    const session = await ds.auth.getSession();
    if (session) showApp(session);
    else showLogin();
  }

  main();
})(window.PPE);
