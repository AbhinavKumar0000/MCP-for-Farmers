/**
 * Kisan Intelligence – Crop Advisor
 * Fully integrated with FastAPI POST /analyze
 * Renders per-tool metric cards, a step tracker, and markdown LLM output.
 */
(() => {
  /* ── DOM refs ─────────────────────────────────────────── */
  const modeManualBtn  = document.getElementById("mode-manual-btn");
  const modeMapBtn     = document.getElementById("mode-map-btn");
  const mapWrapper     = document.getElementById("map-wrapper");
  const form           = document.getElementById("analyze-form");
  const latInput       = document.getElementById("latitude-input");
  const lonInput       = document.getElementById("longitude-input");
  const submitBtn      = document.getElementById("submit-btn");
  const errorBanner    = document.getElementById("error-banner");
  const toolsOutputEl  = document.getElementById("tools-output");
  const llmOutputEl    = document.getElementById("llm-output");
  const summaryCardEl  = document.getElementById("summary-card");
  const loadingOverlay = document.getElementById("loading-overlay");
  const stepTracker    = document.getElementById("step-tracker");
  const stepList       = document.getElementById("step-list");

  let map, marker, mapInitialized = false;

  /* ── Map mode ───────────────────────────────────────────── */
  function setMode(mode) {
    const isMap = mode === "map";
    modeManualBtn.classList.toggle("toggle-chip-active", !isMap);
    modeMapBtn.classList.toggle("toggle-chip-active", isMap);
    modeManualBtn.setAttribute("aria-selected", String(!isMap));
    modeMapBtn.setAttribute("aria-selected", String(isMap));
    if (isMap) {
      mapWrapper.classList.add("map-wrapper-visible");
      mapWrapper.setAttribute("aria-hidden", "false");
      if (!mapInitialized) initMap();
    } else {
      mapWrapper.classList.remove("map-wrapper-visible");
      mapWrapper.setAttribute("aria-hidden", "true");
    }
  }

  function initMap() {
    const mapEl = document.getElementById("mp-map");
    if (!mapEl || typeof L === "undefined") return;
    map = L.map(mapEl, { zoomControl: true, attributionControl: false }).setView([23.5, 78.5], 6.3);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(map);
    fetch("/mp-geojson")
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data) return;
        L.geoJSON(data, {
          style: () => ({ color: "#f97316", weight: 1.2, fillColor: "#f97316", fillOpacity: 0.06 }),
        }).addTo(map);
      })
      .catch(() => {});
    map.on("click", e => {
      const { lat, lng } = e.latlng;
      if (!marker) marker = L.marker(e.latlng, { keyboard: false }).addTo(map);
      else marker.setLatLng(e.latlng);
      latInput.value = lat.toFixed(6);
      lonInput.value = lng.toFixed(6);
    });
    mapInitialized = true;
  }

  /* ── Loading state ──────────────────────────────────────── */
  function showLoading(show) {
    if (!loadingOverlay) return;
    if (show) {
      loadingOverlay.classList.add("loading-overlay-visible");
    } else {
      loadingOverlay.classList.remove("loading-overlay-visible");
    }
    submitBtn.disabled = show;
  }

  /* ── Error banner ───────────────────────────────────────── */
  function setError(message) {
    if (!message) { errorBanner.hidden = true; errorBanner.textContent = ""; return; }
    errorBanner.hidden = false;
    errorBanner.textContent = message;
  }

  /* ── Summary card ───────────────────────────────────────── */
  function renderSummary(data) {
    if (!data?.location) { summaryCardEl.hidden = true; return; }
    const { latitude, longitude } = data.location;
    const order = Array.isArray(data.tool_execution_order) ? data.tool_execution_order : [];
    summaryCardEl.innerHTML = `
      <div class="summary-title">Location analyzed</div>
      <div class="summary-meta">
        <span class="summary-pill">Lat ${latitude.toFixed(4)}°, Lon ${longitude.toFixed(4)}°</span>
        <span class="summary-pill">${order.length} tools called</span>
      </div>`;
    summaryCardEl.hidden = false;
  }

  /* ── Step tracker ───────────────────────────────────────── */
  const TOOL_LABELS = {
    weather_tool:           "Weather",
    climate_tool:           "Climate",
    season_tool:            "Season",
    risk_tool:              "Risk",
    get_soil_by_coordinates:"Soil",
  };

  function renderStepTracker(order) {
    if (!order?.length) { stepTracker.hidden = true; return; }
    stepList.innerHTML = "";
    order.forEach(name => {
      const chip = document.createElement("span");
      chip.className = "step-chip step-chip-done";
      chip.textContent = TOOL_LABELS[name] || name;
      stepList.appendChild(chip);
    });
    stepTracker.hidden = false;
  }

  /* ── Helpers ────────────────────────────────────────────── */
  function fmt(v, decimals = 1) {
    if (v === null || v === undefined) return "—";
    return typeof v === "number" ? v.toFixed(decimals) : String(v);
  }

  function pct(prob) {
    if (prob === null || prob === undefined) return "—";
    return Math.round(prob * 100) + "%";
  }

  function severityClass(prob) {
    if (prob === null || prob === undefined) return "sev-low";
    if (prob >= 0.40) return "sev-high";
    if (prob >= 0.20) return "sev-mod";
    return "sev-low";
  }
  function severityLabel(prob) {
    if (prob === null || prob === undefined) return "—";
    if (prob >= 0.40) return "High";
    if (prob >= 0.20) return "Moderate";
    return "Low";
  }

  function riskBarColor(prob) {
    if (prob >= 0.40) return "#ef4444";
    if (prob >= 0.20) return "#eab308";
    return "#22c55e";
  }

  function makeCard(extraClass = "") {
    const el = document.createElement("article");
    el.className = "tool-card" + (extraClass ? " " + extraClass : "");
    return el;
  }

  function cardHeader(icon, title, badgeText) {
    const iconSpan = icon
      ? `<span class="tool-card-icon">${icon}</span>`
      : "";
    return `
      <div class="tool-card-header">
        <div class="tool-card-name">
          ${iconSpan}
          <span class="tool-card-title">${title}</span>
        </div>
        ${badgeText ? `<span class="tool-badge">${badgeText}</span>` : ""}
      </div>`;
  }

  function metricRow(label, value, accent = false) {
    return `<div class="metric-row">
      <span class="metric-label">${label}</span>
      <span class="metric-value${accent ? " metric-value-accent" : ""}">${value}</span>
    </div>`;
  }

  /* ── Per-tool renderers ─────────────────────────────────── */

  function renderWeather(d) {
    const card = makeCard();
    card.innerHTML = cardHeader("", "Weather", d.data_period || "forecast") + `
      <div class="metric-list">
        ${metricRow("Avg temperature", fmt(d.avg_temp_c) + " °C", true)}
        ${metricRow("Min / Max", fmt(d.min_temp_c) + " / " + fmt(d.max_temp_c) + " °C")}
        ${metricRow("Forecast rainfall (7d)", fmt(d.total_rainfall_mm) + " mm")}
        ${metricRow("Past 7d rainfall", fmt(d.rainfall_past_7d_mm) + " mm")}
        ${metricRow("Humidity", fmt(d.avg_humidity_percent, 0) + " %")}
        ${d.precipitation_probability_max_7d_percent != null ? metricRow("Precip. probability", d.precipitation_probability_max_7d_percent + " %") : ""}
      </div>`;
    return card;
  }

  function renderClimate(d) {
    const card = makeCard();
    card.innerHTML = cardHeader("", "Climate normals", d.data_period_years || "2014-2023") + `
      <div class="metric-list">
        ${metricRow("Avg temp (normal)", fmt(d.avg_temp_normal_c) + " °C", true)}
        ${metricRow("Avg rainfall/month", fmt(d.avg_rainfall_normal_mm) + " mm")}
        ${metricRow("Temp std dev", d.temp_std_dev_c != null ? fmt(d.temp_std_dev_c) + " °C" : "—")}
        ${metricRow("Precip std dev", d.precipitation_std_dev_mm != null ? fmt(d.precipitation_std_dev_mm) + " mm" : "—")}
      </div>`;
    return card;
  }

  function renderSeason(d) {
    const SEASON_ICONS = { Kharif: "Kharif", Rabi: "Rabi", Zaid: "Zaid" };
    const icon = SEASON_ICONS[d.current_season] || "Season";
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const monthName = d.current_month >= 1 && d.current_month <= 12 ? months[d.current_month - 1] : d.current_month;
    const card = makeCard();
    card.innerHTML = cardHeader("", "Season", "Cropping") + `
      <div class="season-display">
          <div class="season-icon-big">${icon}</div>
        <div class="season-info">
          <div class="season-name">${d.current_season || "—"}</div>
          <div class="season-sub">Month: ${monthName}</div>
          <div class="season-sub">Kharif: Jun–Oct &nbsp;|&nbsp; Rabi: Nov–Mar &nbsp;|&nbsp; Zaid: Apr–May</div>
        </div>
      </div>`;
    return card;
  }

  function renderRisk(d) {
    const card = makeCard();
    const drought = d.drought_probability;
    const flood   = d.flood_probability;
    const heat    = d.heatwave_probability;

    function bar(prob, label, extra = "") {
      const width = prob != null ? Math.round(prob * 100) : 0;
      const color = riskBarColor(prob);
      const sev = d[`${label.toLowerCase().replace(" ", "_")}_severity`] || severityLabel(prob);
      return `
        <div class="risk-row">
          <div class="risk-row-header">
            <span class="risk-row-label">${label}</span>
            <span class="risk-row-value">
              ${pct(prob)}
              <span class="severity-badge ${severityClass(prob)}">${sev}</span>
            </span>
          </div>
          <div class="risk-bar-track">
            <div class="risk-bar-fill" style="width:${width}%;background:${color}"></div>
          </div>
        </div>`;
    }

    card.innerHTML = cardHeader("", "Risk analysis", d.data_period || "") + `
      <div class="risk-bar-container">
        ${bar(drought, "Drought")}
        ${bar(flood,   "Flood")}
        ${bar(heat,    "Heatwave")}
      </div>
      ${d.precipitation_30d_mm != null ? `<div class="metric-list" style="margin-top:8px">${metricRow("30-day rainfall", fmt(d.precipitation_30d_mm) + " mm")}</div>` : ""}`;
    return card;
  }

  function renderSoil(d) {
    if (d.error) {
      const card = makeCard("tool-card-soil");
      card.innerHTML = cardHeader("", "Soil", "Outside MP") + `
        <p class="soil-desc">${d.error}</p>`;
      return card;
    }
    const card = makeCard("tool-card-soil");
    card.innerHTML = cardHeader("", "Soil", "District data") + `
      <p class="soil-district">${d.district || "—"}</p>
      <p class="soil-type">${d.dominant_soil || "—"}</p>
      <p class="soil-desc">${d.soil_characteristics || "—"}</p>`;
    return card;
  }

  /* ── Tool output dispatcher ─────────────────────────────── */
  const TOOL_RENDERERS = {
    weather:        renderWeather,
    climate_normals: renderClimate,
    season:         renderSeason,
    risk_analysis:  renderRisk,
    get_soil_by_coordinates: renderSoil,
    // fallback key aliases
    climate_tool:   renderClimate,
    weather_tool:   renderWeather,
    season_tool:    renderSeason,
    risk_tool:      renderRisk,
  };

  /* Key aliases to normalize tool names from the API response */
  const KEY_NORMALIZE = {
    weather_tool:                "weather",
    climate_tool:                "climate_normals",
    season_tool:                 "season",
    risk_tool:                   "risk_analysis",
    get_soil_by_coordinates:     "get_soil_by_coordinates",
  };

  function renderToolOutputs(toolsOutput, order) {
    toolsOutputEl.innerHTML = "";
    if (!toolsOutput || !Object.keys(toolsOutput).length) {
      toolsOutputEl.innerHTML = `
        <div class="output-empty">
          <div class="output-empty-text">No tool results yet. Run an analysis first.</div>
        </div>`;
      return;
    }

    // Render in execution order, then any extra keys
    const rendered = new Set();
    const renderKey = (rawKey) => {
      if (rendered.has(rawKey)) return;
      rendered.add(rawKey);
      const data = toolsOutput[rawKey];
      if (!data) return;

      // Determine renderer: check data.tool field first, then key
      const toolField = data.tool || rawKey;
      const renderer =
        TOOL_RENDERERS[toolField] ||
        TOOL_RENDERERS[KEY_NORMALIZE[rawKey]] ||
        TOOL_RENDERERS[rawKey];

      if (renderer) {
        toolsOutputEl.appendChild(renderer(data));
      } else {
        // Generic fallback card for unknown tools
        const card = makeCard();
        card.innerHTML = cardHeader("", rawKey, "Tool output") + `
          <pre style="font-size:11px;color:rgba(229,231,235,0.9);white-space:pre-wrap;word-break:break-word;margin:0">${
            JSON.stringify(data, null, 2)
          }</pre>`;
        toolsOutputEl.appendChild(card);
      }
    };

    // Use execution order if available
    if (order?.length) order.forEach(renderKey);
    // Catch any tool not in execution order
    Object.keys(toolsOutput).forEach(renderKey);
  }

  /* ── LLM output (markdown) ──────────────────────────────── */
  function renderLlmOutput(text) {
    const content = (text || "").trim();
    if (!content || content === "(No final message from model)") {
      llmOutputEl.classList.add("llm-card-empty");
      llmOutputEl.innerHTML = '<p class="llm-placeholder">No LLM output. Run an analysis first.</p>';
      return;
    }
    llmOutputEl.classList.remove("llm-card-empty");
    // Use marked.js if available, otherwise fallback to escaped plain text
    if (typeof marked !== "undefined") {
      try {
        const html = marked.parse(content, { breaks: true, gfm: true });
        llmOutputEl.innerHTML = `<div class="llm-content">${html}</div>`;
        return;
      } catch (_) { /* fall through to plain text */ }
    }
    // Plain-text fallback
    llmOutputEl.innerHTML = `<div class="llm-content">${
      content.replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br />")
    }</div>`;
  }

  /* ── Form submit ─────────────────────────────────────────── */
  async function handleSubmit(event) {
    event.preventDefault();
    setError("");

    const lat = Number(latInput.value);
    const lon = Number(lonInput.value);
    if (Number.isNaN(lat) || Number.isNaN(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      setError("Please provide a valid latitude (−90 to 90) and longitude (−180 to 180).");
      return;
    }

    showLoading(true);
    stepTracker.hidden = true;
    stepList.innerHTML = "";
    summaryCardEl.hidden = true;
    toolsOutputEl.innerHTML = "";

    try {
      const res = await fetch("/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ latitude: lat, longitude: lon }),
      });

      if (!res.ok) {
        let detail = res.statusText;
        try {
          const errJson = await res.json();
          detail = errJson.detail || detail;
        } catch (_) {}
        throw new Error(detail || "Request failed");
      }

      const data = await res.json();
      const order = Array.isArray(data.tool_execution_order) ? data.tool_execution_order : [];

      renderStepTracker(order);
      renderSummary(data);
      renderToolOutputs(data.tools_output, order);
      renderLlmOutput(data.llm_final_message);

    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      showLoading(false);
    }
  }

  /* ── Init ───────────────────────────────────────────────── */
  modeManualBtn.addEventListener("click", () => setMode("manual"));
  modeMapBtn.addEventListener("click",   () => setMode("map"));
  form.addEventListener("submit", handleSubmit);
  setMode("manual");
})();
