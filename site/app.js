const state = {
  availability: null,
  filter: "",
  history: [],
  prices: null,
  summary: null,
  tab: "overview",
};

const currencyFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 6,
  minimumFractionDigits: 0,
});

const integerFormatter = new Intl.NumberFormat("en-US");

const elements = {
  lastRun: document.querySelector("#last-run"),
  metrics: document.querySelector("#metrics"),
  search: document.querySelector("#search"),
  summaryLink: document.querySelector("#summary-link"),
  tabs: document.querySelectorAll(".tab"),
  view: document.querySelector("#view"),
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    };
    return entities[char];
  });
}

async function fetchWithFallback(path, parser = "json") {
  const candidates = dataCandidates(path);
  let lastError = null;
  for (const candidate of candidates) {
    try {
      const response = await fetch(candidate, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`);
      }
      return parser === "text" ? response.text() : response.json();
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
}

function dataCandidates(path) {
  return [path, `../${path}`, `/${path}`];
}

async function resolveHref(path) {
  for (const candidate of dataCandidates(path)) {
    try {
      const response = await fetch(candidate, {
        cache: "no-store",
        method: "HEAD",
      });
      if (response.ok) {
        return candidate;
      }
    } catch {
      // Try the next local/deployed layout candidate.
    }
  }
  return path;
}

function formatInteger(value) {
  return integerFormatter.format(Number(value || 0));
}

function formatPrice(value, currency = "USD") {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return `${currency} ${currencyFormatter.format(Number(value))}`;
}

function formatDate(value) {
  if (!value) {
    return "Unknown run time";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function stateCount(counts, name) {
  return Number(counts?.[name] || 0);
}

function matches(values) {
  const needle = state.filter.trim().toLowerCase();
  if (!needle) {
    return true;
  }
  return values.some((value) => String(value ?? "").toLowerCase().includes(needle));
}

function metric(label, value, detail) {
  return `
    <div class="stat rounded-box border border-base-300 bg-base-100 shadow-sm">
      <div class="stat-title text-xs font-bold uppercase">${escapeHtml(label)}</div>
      <div class="stat-value text-2xl lg:text-3xl">${escapeHtml(value)}</div>
      <div class="stat-desc">${escapeHtml(detail)}</div>
    </div>
  `;
}

function renderMetrics() {
  const availability = state.summary.availability.summary;
  const price = state.summary.price;
  elements.lastRun.textContent = `Updated ${formatDate(state.summary.generatedAt)}`;
  elements.metrics.innerHTML = [
    metric("Regions", formatInteger(availability.regionCount), `${formatInteger(availability.geographyCount)} geographies`),
    metric("Offerings", formatInteger(availability.offeringCount), `${formatInteger(availability.skuCount)} product/SKU rows`),
    metric("Availability rows", formatInteger(availability.rowCount), hashShort(state.summary.availability.dataHash)),
    metric("Price items", formatInteger(price.itemCount), `${formatInteger(price.queryCount)} tracked queries`),
    metric("Price changes", formatInteger(price.changes.priceChanged), `${formatInteger(price.changes.added)} added, ${formatInteger(price.changes.removed)} removed`),
  ].join("");
}

function hashShort(hash) {
  return hash ? hash.slice(0, 12) : "no hash";
}

const numericHeaders = new Set([
  "Avg",
  "Closing down",
  "GA",
  "Geographies",
  "Items",
  "Max",
  "Min",
  "Min price",
  "Offerings",
  "Preview",
  "Price changes",
  "Products",
  "Regions",
  "SKUs",
]);

function table(headers, rows, rowRenderer) {
  if (!rows.length) {
    return `<div class="empty">No rows match the current filter.</div>`;
  }

  const columns = headers.map((header) =>
    typeof header === "string"
      ? {
          className: numericHeaders.has(header) ? "num" : "",
          label: header,
        }
      : header,
  );

  return `
    <div class="table-wrap">
      <table class="table table-sm table-zebra table-pin-rows">
        <thead>
          <tr>
            ${columns
              .map(
                (column) =>
                  `<th class="${escapeHtml(column.className || "")}">${escapeHtml(column.label)}</th>`,
              )
              .join("")}
          </tr>
        </thead>
        <tbody>
          ${rows.map(rowRenderer).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function bar(value, max) {
  const safeMax = Math.max(Number(max || 0), 1);
  return `
    <div class="value-bar">
      <span class="text-right">${formatInteger(value)}</span>
      <progress class="progress progress-primary" value="${Number(value || 0)}" max="${safeMax}"></progress>
    </div>
  `;
}

function panel(title, subtitle, content) {
  return `
    <section class="card min-w-0 border border-base-300 bg-base-100 shadow-sm">
      <div class="border-b border-base-300 px-4 py-3">
        <div class="flex items-baseline justify-between gap-3">
          <h2 class="font-semibold">${escapeHtml(title)}</h2>
          <span class="text-sm text-base-content/60">${escapeHtml(subtitle || "")}</span>
        </div>
      </div>
      ${content}
    </section>
  `;
}

function statusLine() {
  const availability = state.summary.availability;
  const price = state.summary.price;
  const source = state.summary.sources.availability;
  return `
    <div class="mb-4 flex flex-wrap gap-2">
      <span class="badge badge-outline badge-success">availability ${hashShort(availability.dataHash)}</span>
      <span class="badge badge-outline badge-success">prices ${hashShort(price.dataHash)}</span>
      <span class="badge badge-outline ${price.failures.length ? "badge-error" : "badge-success"}">${formatInteger(price.failures.length)} price failures</span>
      <span class="badge badge-outline">source modified ${source.lastModified || "unknown"}</span>
    </div>
  `;
}

function renderOverview() {
  const availability = state.summary.availability.summary;
  const price = state.summary.price;
  const maxRegionOfferings = Math.max(...availability.topRegionsByOfferingCount.map((row) => row.offeringCount), 1);
  const maxOfferingRegions = Math.max(...availability.topOfferingsByRegionCount.map((row) => row.regionCount), 1);
  const topRegions = availability.topRegionsByOfferingCount.slice(0, 12);
  const topOfferings = availability.topOfferingsByRegionCount.slice(0, 12);
  const priceRows = price.querySummaries
    .filter((row) => matches([row.service, row.region]))
    .slice(0, 18);
  const historyRows = state.history.slice(-12).reverse();

  elements.view.innerHTML = `
    ${statusLine()}
    <div class="grid gap-4 xl:grid-cols-2">
      ${panel(
        "Largest regional menus",
        "offerings by region",
        table(["Region", "Geography", "Offerings", "Preview"], topRegions, (row) => `
          <tr>
            <td>${escapeHtml(row.region)}</td>
            <td>${escapeHtml(row.geography)}</td>
            <td>${bar(row.offeringCount, maxRegionOfferings)}</td>
            <td class="num">${formatInteger(stateCount(row.stateCounts, "Preview"))}</td>
          </tr>
        `),
      )}
      ${panel(
        "Most widespread services",
        "regions by offering",
        table(["Offering", "Regions", "SKUs", "Preview"], topOfferings, (row) => `
          <tr>
            <td>${escapeHtml(row.offering)}</td>
            <td>${bar(row.regionCount, maxOfferingRegions)}</td>
            <td class="num">${formatInteger(row.skuCount)}</td>
            <td class="num">${formatInteger(stateCount(row.stateCounts, "Preview"))}</td>
          </tr>
        `),
      )}
      ${panel(
        "Tracked price floor",
        "sampled retail meters",
        table(["Service", "Region", "Items", "Min price"], priceRows, (row) => `
          <tr>
            <td>${escapeHtml(row.service)}</td>
            <td>${escapeHtml(row.region)}</td>
            <td class="num">${formatInteger(row.itemCount)}${row.truncated ? " +" : ""}</td>
            <td class="num">${escapeHtml(formatPrice(row.minRetailPrice, row.currency))}</td>
          </tr>
        `),
      )}
      ${panel(
        "Run history",
        "daily archive rows",
        table(["Date", "Regions", "Offerings", "Price changes"], historyRows, (row) => `
          <tr>
            <td>${escapeHtml(row.date)}</td>
            <td class="num">${formatInteger(row.regions)}</td>
            <td class="num">${formatInteger(row.offerings)}</td>
            <td class="num">${formatInteger(row.priceChanged)}</td>
          </tr>
        `),
      )}
    </div>
  `;
}

function renderRegions() {
  const regions = state.summary.availability.summary.regions.filter((row) =>
    matches([row.region, row.geography, row.regionFlags?.join(" ")]),
  );
  elements.view.innerHTML = `
    ${statusLine()}
    ${table(["Region", "Geography", "Offerings", "SKUs", "GA", "Preview", "Flags"], regions, (row) => `
      <tr>
        <td>${escapeHtml(row.region)}</td>
        <td>${escapeHtml(row.geography)}</td>
        <td class="num">${formatInteger(row.offeringCount)}</td>
        <td class="num">${formatInteger(row.skuCount)}</td>
        <td class="num">${formatInteger(stateCount(row.stateCounts, "GA"))}</td>
        <td class="num">${formatInteger(stateCount(row.stateCounts, "Preview"))}</td>
        <td>${escapeHtml((row.regionFlags || []).join(", "))}</td>
      </tr>
    `)}
  `;
}

function renderServices() {
  const offerings = state.summary.availability.summary.offerings.filter((row) =>
    matches([row.offering]),
  );
  elements.view.innerHTML = `
    ${statusLine()}
    ${table(["Offering", "Regions", "SKUs", "Geographies", "GA", "Preview", "Closing down"], offerings, (row) => `
      <tr>
        <td>${escapeHtml(row.offering)}</td>
        <td class="num">${formatInteger(row.regionCount)}</td>
        <td class="num">${formatInteger(row.skuCount)}</td>
        <td class="num">${formatInteger(row.geographyCount)}</td>
        <td class="num">${formatInteger(stateCount(row.stateCounts, "GA"))}</td>
        <td class="num">${formatInteger(stateCount(row.stateCounts, "Preview"))}</td>
        <td class="num">${formatInteger(stateCount(row.stateCounts, "Closing Down"))}</td>
      </tr>
    `)}
  `;
}

function renderPrices() {
  const rows = state.summary.price.querySummaries.filter((row) =>
    matches([row.service, row.region]),
  );
  elements.view.innerHTML = `
    ${statusLine()}
    ${table(["Service", "Region", "Items", "Products", "SKUs", "Min", "Avg", "Max", "Sample"], rows, (row) => `
      <tr>
        <td>${escapeHtml(row.service)}</td>
        <td>${escapeHtml(row.region)}</td>
        <td class="num">${formatInteger(row.itemCount)}${row.truncated ? " +" : ""}</td>
        <td class="num">${formatInteger(row.productCount)}</td>
        <td class="num">${formatInteger(row.skuCount)}</td>
        <td class="num">${escapeHtml(formatPrice(row.minRetailPrice, row.currency))}</td>
        <td class="num">${escapeHtml(formatPrice(row.averageRetailPrice, row.currency))}</td>
        <td class="num">${escapeHtml(formatPrice(row.maxRetailPrice, row.currency))}</td>
        <td>${escapeHtml((row.cheapestSamples || [])[0]?.productName || "")}</td>
      </tr>
    `)}
  `;
}

function changePanel(title, count, samples, renderer) {
  const rows = samples.length
    ? samples.map(renderer).join("")
    : `<div class="empty">No changes recorded in this category.</div>`;
  return panel(title, `${formatInteger(count)} total`, `<div class="change-list">${rows}</div>`);
}

function renderChanges() {
  const availability = state.summary.availability.changes;
  const price = state.summary.price.changes;
  elements.view.innerHTML = `
    ${statusLine()}
    <div class="grid gap-4 xl:grid-cols-2">
      ${changePanel("Availability added", availability.added, availability.addedSamples || [], (row) => `
        <div class="change-item">
          <div>
            <strong>${escapeHtml(row.offering)}</strong>
            <span>${escapeHtml(row.region)} ${escapeHtml(row.sku || "")}</span>
          </div>
          <span>${escapeHtml(row.state)}</span>
        </div>
      `)}
      ${changePanel("Availability removed", availability.removed, availability.removedSamples || [], (row) => `
        <div class="change-item">
          <div>
            <strong>${escapeHtml(row.offering)}</strong>
            <span>${escapeHtml(row.region)} ${escapeHtml(row.sku || "")}</span>
          </div>
          <span>${escapeHtml(row.state)}</span>
        </div>
      `)}
      ${changePanel("State changes", availability.stateChanged, availability.stateChangedSamples || [], (row) => `
        <div class="change-item">
          <div>
            <strong>${escapeHtml(row.offering)}</strong>
            <span>${escapeHtml(row.region)} ${escapeHtml(row.sku || "")}</span>
          </div>
          <span>${escapeHtml(row.from)} -> ${escapeHtml(row.to)}</span>
        </div>
      `)}
      ${changePanel("Price changes", price.priceChanged, price.priceChangedSamples || [], (row) => `
        <div class="change-item">
          <div>
            <strong>${escapeHtml(row.service)}</strong>
            <span>${escapeHtml(row.region)} ${escapeHtml(row.productName || "")} ${escapeHtml(row.skuName || "")}</span>
          </div>
          <span>${escapeHtml(formatPrice(row.from))} -> ${escapeHtml(formatPrice(row.to))}</span>
        </div>
      `)}
    </div>
  `;
}

function render() {
  if (!state.summary) {
    return;
  }
  renderMetrics();
  if (state.tab === "regions") {
    renderRegions();
  } else if (state.tab === "services") {
    renderServices();
  } else if (state.tab === "prices") {
    renderPrices();
  } else if (state.tab === "changes") {
    renderChanges();
  } else {
    renderOverview();
  }
}

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/).filter(Boolean);
  if (!lines.length) {
    return [];
  }
  const headers = lines.shift().split(",");
  return lines.map((line) => {
    const values = line.split(",");
    return Object.fromEntries(headers.map((header, index) => [header, values[index] || ""]));
  });
}

async function load() {
  try {
    if (elements.summaryLink) {
      elements.summaryLink.href = await resolveHref("data/gold/latest/summary.json");
    }
    const [summary, availability, prices, historyText] = await Promise.all([
      fetchWithFallback("data/gold/latest/summary.json"),
      fetchWithFallback("data/silver/latest/availability.json"),
      fetchWithFallback("data/silver/latest/prices.json"),
      fetchWithFallback("data/gold/timeseries/daily-summary.csv", "text").catch(() => ""),
    ]);
    state.summary = summary;
    state.availability = availability;
    state.prices = prices;
    state.history = historyText ? parseCsv(historyText) : [];
    render();
  } catch (error) {
    elements.view.innerHTML = `
      <div class="empty">
        Could not load archive data: ${escapeHtml(error.message || error)}
      </div>
    `;
  }
}

elements.tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    elements.tabs.forEach((candidate) => candidate.classList.remove("tab-active"));
    tab.classList.add("tab-active");
    state.tab = tab.dataset.tab;
    render();
  });
});

elements.search.addEventListener("input", (event) => {
  state.filter = event.target.value;
  render();
});

load();
