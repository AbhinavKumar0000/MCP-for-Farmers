/**
 * Kisan Intelligence - Crop Advisor
 * Fully integrated with FastAPI POST /analyze.
 * Renders per-tool metric cards, a step tracker, and markdown LLM output.
 */
(() => {
  const modeManualBtn = document.getElementById("mode-manual-btn");
  const modeMapBtn = document.getElementById("mode-map-btn");
  const mapWrapper = document.getElementById("map-wrapper");
  const form = document.getElementById("analyze-form");
  const latInput = document.getElementById("latitude-input");
  const lonInput = document.getElementById("longitude-input");
  const submitBtn = document.getElementById("submit-btn");
  const errorBanner = document.getElementById("error-banner");
  const toolsOutputEl = document.getElementById("tools-output");
  const llmOutputEl = document.getElementById("llm-output");
  const summaryCardEl = document.getElementById("summary-card");
  const loadingOverlay = document.getElementById("loading-overlay");
  const stepTracker = document.getElementById("step-tracker");
  const stepList = document.getElementById("step-list");

  let map;
  let marker;
  let mapInitialized = false;

  function setSanitizedHtml(element, html) {
    element.innerHTML = DOMPurify.sanitize(html);
  }

  function setMode(mode) {
    const isMap = mode === "map";
    modeManualBtn.classList.toggle("toggle-chip-active", !isMap);
    modeMapBtn.classList.toggle("toggle-chip-active", isMap);
    modeManualBtn.setAttribute("aria-selected", String(!isMap));
    modeMapBtn.setAttribute("aria-selected", String(isMap));
    if (isMap) {
      mapWrapper.classList.add("map-wrapper-visible");
      mapWrapper.setAttribute("aria-hidden", "false");
      if (!mapInitialized) {
        initMap();
      }
    } else {
      mapWrapper.classList.remove("map-wrapper-visible");
      mapWrapper.setAttribute("aria-hidden", "true");
    }
  }

  function initMap() {
    const mapEl = document.getElementById("mp-map");
    if (!mapEl || typeof L === "undefined") {
      return;
    }
    map = L.map(mapEl, { zoomControl: true, attributionControl: false }).setView([23.5, 78.5], 6.3);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(map);

    fetch("/mp-geojson")
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!data) {
          return;
        }
        L.geoJSON(data, {
          style: () => ({ color: "#f97316", weight: 1.2, fillColor: "#f97316", fillOpacity: 0.06 }),
        }).addTo(map);
      })
      .catch(() => {});

    map.on("click", (event) => {
      const { lat, lng } = event.latlng;
      if (!marker) {
        marker = L.marker(event.latlng, { keyboard: false }).addTo(map);
      } else {
        marker.setLatLng(event.latlng);
      }
      latInput.value = lat.toFixed(6);
      lonInput.value = lng.toFixed(6);
    });

    mapInitialized = true;
  }

  function showLoading(show) {
    if (!loadingOverlay) {
      return;
    }
    if (show) {
      loadingOverlay.classList.add("loading-overlay-visible");
    } else {
      loadingOverlay.classList.remove("loading-overlay-visible");
    }
    submitBtn.disabled = show;
  }

  function setError(message) {
    if (!message) {
      errorBanner.hidden = true;
      errorBanner.textContent = "";
      return;
    }
    errorBanner.hidden = false;
    errorBanner.textContent = message;
  }

  function renderSummary(data) {
    if (!data?.location) {
      summaryCardEl.hidden = true;
      return;
    }
    const { latitude, longitude } = data.location;
    const order = Array.isArray(data.tool_execution_order) ? data.tool_execution_order : [];
    setSanitizedHtml(
      summaryCardEl,
      `
        <div class="summary-title">Location analyzed</div>
        <div class="summary-meta">
          <span class="summary-pill">Lat ${latitude.toFixed(4)} deg, Lon ${longitude.toFixed(4)} deg</span>
          <span class="summary-pill">${order.length} tools called</span>
        </div>`,
    );
    summaryCardEl.hidden = false;
  }

  const TOOL_LABELS = {
    weather_tool: "Weather",
    climate_tool: "Climate",
    season_tool: "Season",
    risk_tool: "Risk",
    get_soil_by_coordinates: "Soil",
    crop_score: "Crop score",
  };

  function renderStepTracker(order) {
    if (!order?.length) {
      stepTracker.hidden = true;
      return;
    }
    stepList.innerHTML = "";
    order.forEach((name) => {
      const chip = document.createElement("span");
      chip.className = "step-chip step-chip-done";
      chip.textContent = TOOL_LABELS[name] || name;
      stepList.appendChild(chip);
    });
    stepTracker.hidden = false;
  }

  function fmt(value, decimals = 1) {
    if (value === null || value === undefined) {
      return "-";
    }
    return typeof value === "number" ? value.toFixed(decimals) : String(value);
  }

  function pct(probability) {
    if (probability === null || probability === undefined) {
      return "-";
    }
    return `${Math.round(probability * 100)}%`;
  }

  function severityClass(probability) {
    if (probability === null || probability === undefined) {
      return "sev-low";
    }
    if (probability >= 0.4) {
      return "sev-high";
    }
    if (probability >= 0.2) {
      return "sev-mod";
    }
    return "sev-low";
  }

  function severityLabel(probability) {
    if (probability === null || probability === undefined) {
      return "-";
    }
    if (probability >= 0.4) {
      return "High";
    }
    if (probability >= 0.2) {
      return "Moderate";
    }
    return "Low";
  }

  function riskBarColor(probability) {
    if (probability >= 0.4) {
      return "#ef4444";
    }
    if (probability >= 0.2) {
      return "#eab308";
    }
    return "#22c55e";
  }

  function makeCard(extraClass = "") {
    const element = document.createElement("article");
    element.className = `tool-card${extraClass ? ` ${extraClass}` : ""}`;
    return element;
  }

  function cardHeader(icon, title, badgeText) {
    const iconSpan = icon ? `<span class="tool-card-icon">${icon}</span>` : "";
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

  function renderWeather(data) {
    const card = makeCard();
    setSanitizedHtml(
      card,
      cardHeader("", "Weather", data.data_period || "forecast") + `
        <div class="metric-list">
          ${metricRow("Avg temperature", `${fmt(data.avg_temp_c)} C`, true)}
          ${metricRow("Min / Max", `${fmt(data.min_temp_c)} / ${fmt(data.max_temp_c)} C`)}
          ${metricRow("Forecast rainfall (7d)", `${fmt(data.total_rainfall_mm)} mm`)}
          ${metricRow("Past 7d rainfall", `${fmt(data.rainfall_past_7d_mm)} mm`)}
          ${metricRow("Humidity", `${fmt(data.avg_humidity_percent, 0)} %`)}
          ${data.precipitation_probability_max_7d_percent != null ? metricRow("Precip. probability", `${data.precipitation_probability_max_7d_percent} %`) : ""}
        </div>`,
    );
    return card;
  }

  function renderClimate(data) {
    const card = makeCard();
    setSanitizedHtml(
      card,
      cardHeader("", "Climate normals", data.data_period_years || "2014-2023") + `
        <div class="metric-list">
          ${metricRow("Avg temp (normal)", `${fmt(data.avg_temp_normal_c)} C`, true)}
          ${metricRow("Avg rainfall/month", `${fmt(data.avg_rainfall_normal_mm)} mm`)}
          ${metricRow("Temp std dev", data.temp_std_dev_c != null ? `${fmt(data.temp_std_dev_c)} C` : "-")}
          ${metricRow("Precip std dev", data.precipitation_std_dev_mm != null ? `${fmt(data.precipitation_std_dev_mm)} mm` : "-")}
        </div>`,
    );
    return card;
  }

  function renderSeason(data) {
    const icon = data.current_season || "Season";
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const monthName = data.current_month >= 1 && data.current_month <= 12 ? months[data.current_month - 1] : data.current_month;
    const card = makeCard();
    setSanitizedHtml(
      card,
      cardHeader("", "Season", "Cropping") + `
        <div class="season-display">
          <div class="season-icon-big">${icon}</div>
          <div class="season-info">
            <div class="season-name">${data.current_season || "-"}</div>
            <div class="season-sub">Month: ${monthName}</div>
            <div class="season-sub">Kharif: Jun-Oct | Rabi: Nov-Mar | Zaid: Apr-May</div>
          </div>
        </div>`,
    );
    return card;
  }

  function renderRisk(data) {
    const card = makeCard();
    const drought = data.drought_probability;
    const flood = data.flood_probability;
    const heatwave = data.heatwave_probability;

    function bar(probability, label) {
      const width = probability != null ? Math.round(probability * 100) : 0;
      const color = riskBarColor(probability);
      const severity = data[`${label.toLowerCase().replace(" ", "_")}_severity`] || severityLabel(probability);
      return `
        <div class="risk-row">
          <div class="risk-row-header">
            <span class="risk-row-label">${label}</span>
            <span class="risk-row-value">
              ${pct(probability)}
              <span class="severity-badge ${severityClass(probability)}">${severity}</span>
            </span>
          </div>
          <div class="risk-bar-track">
            <div class="risk-bar-fill" style="width:${width}%;background:${color}"></div>
          </div>
        </div>`;
    }

    setSanitizedHtml(
      card,
      cardHeader("", "Risk analysis", data.data_period || "") + `
        <div class="risk-bar-container">
          ${bar(drought, "Drought")}
          ${bar(flood, "Flood")}
          ${bar(heatwave, "Heatwave")}
        </div>
        ${data.precipitation_30d_mm != null ? `<div class="metric-list" style="margin-top:8px">${metricRow("30-day rainfall", `${fmt(data.precipitation_30d_mm)} mm`)}</div>` : ""}`,
    );
    return card;
  }

  function renderSoil(data) {
    const card = makeCard("tool-card-soil");
    if (data.error) {
      setSanitizedHtml(
        card,
        cardHeader("", "Soil", "Outside MP") + `
          <p class="soil-desc">${data.error}</p>`,
      );
      return card;
    }

    const suitability = data.agricultural_suitability
      ? `<p class="soil-desc">Suitable crops: ${data.agricultural_suitability}</p>`
      : "";
    setSanitizedHtml(
      card,
      cardHeader("", "Soil", "District data") + `
        <p class="soil-district">${data.district || "-"}</p>
        <p class="soil-type">${data.dominant_soil || "-"}</p>
        <p class="soil-desc">${data.soil_characteristics || "-"}</p>
        ${suitability}`,
    );
    return card;
  }

  const TOOL_RENDERERS = {
    weather: renderWeather,
    climate_normals: renderClimate,
    season: renderSeason,
    risk_analysis: renderRisk,
    get_soil_by_coordinates: renderSoil,
    climate_tool: renderClimate,
    weather_tool: renderWeather,
    season_tool: renderSeason,
    risk_tool: renderRisk,
  };

  const KEY_NORMALIZE = {
    weather_tool: "weather",
    climate_tool: "climate_normals",
    season_tool: "season",
    risk_tool: "risk_analysis",
    get_soil_by_coordinates: "get_soil_by_coordinates",
  };

  function renderToolOutputs(toolsOutput, order) {
    toolsOutputEl.innerHTML = "";
    if (!toolsOutput || !Object.keys(toolsOutput).length) {
      setSanitizedHtml(
        toolsOutputEl,
        `
          <div class="output-empty">
            <div class="output-empty-text">No tool results yet. Run an analysis first.</div>
          </div>`,
      );
      return;
    }

    const rendered = new Set();
    const renderKey = (rawKey) => {
      if (rendered.has(rawKey)) {
        return;
      }
      rendered.add(rawKey);
      const data = toolsOutput[rawKey];
      if (!data) {
        return;
      }

      const toolField = data.tool || rawKey;
      const renderer = TOOL_RENDERERS[toolField] || TOOL_RENDERERS[KEY_NORMALIZE[rawKey]] || TOOL_RENDERERS[rawKey];

      if (renderer) {
        toolsOutputEl.appendChild(renderer(data));
        return;
      }

      const card = makeCard();
      setSanitizedHtml(
        card,
        cardHeader("", rawKey, "Tool output") + `
          <pre style="font-size:11px;color:rgba(229,231,235,0.9);white-space:pre-wrap;word-break:break-word;margin:0">${
            JSON.stringify(data, null, 2)
          }</pre>`,
      );
      toolsOutputEl.appendChild(card);
    };

    if (order?.length) {
      order.forEach(renderKey);
    }
    Object.keys(toolsOutput).forEach(renderKey);
  }

  function renderLlmOutput(text) {
    const content = (text || "").trim();
    if (!content || content === "(No final message from model)") {
      llmOutputEl.classList.add("llm-card-empty");
      setSanitizedHtml(llmOutputEl, '<p class="llm-placeholder">No LLM output. Run an analysis first.</p>');
      return;
    }

    llmOutputEl.classList.remove("llm-card-empty");
    if (typeof marked !== "undefined") {
      try {
        llmOutputEl.innerHTML = DOMPurify.sanitize(
          `<div class="llm-content">${marked.parse(content, { breaks: true, gfm: true })}</div>`,
        );
        return;
      } catch (_) {
        // Fall through to escaped plain text.
      }
    }

    setSanitizedHtml(
      llmOutputEl,
      `<div class="llm-content">${
        content.replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\n/g, "<br />")
      }</div>`,
    );
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");

    const lat = Number(latInput.value);
    const lon = Number(lonInput.value);
    if (Number.isNaN(lat) || Number.isNaN(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      setError("Please provide a valid latitude (-90 to 90) and longitude (-180 to 180).");
      return;
    }

    showLoading(true);
    stepTracker.hidden = true;
    stepList.innerHTML = "";
    summaryCardEl.hidden = true;
    toolsOutputEl.innerHTML = "";

    try {
      const response = await fetch("/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ latitude: lat, longitude: lon }),
      });

      if (!response.ok) {
        let detail = response.statusText;
        try {
          const errorJson = await response.json();
          detail = errorJson.detail || detail;
        } catch (_) {}
        throw new Error(detail || "Request failed");
      }

      const data = await response.json();
      const order = Array.isArray(data.tool_execution_order) ? data.tool_execution_order : [];
      renderStepTracker(order);
      renderSummary(data);
      renderToolOutputs(data.tools_output, order);
      renderLlmOutput(data.llm_final_message);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Something went wrong. Please try again.");
    } finally {
      showLoading(false);
    }
  }

  modeManualBtn.addEventListener("click", () => setMode("manual"));
  modeMapBtn.addEventListener("click", () => setMode("map"));
  form.addEventListener("submit", handleSubmit);
  setMode("manual");
})();
