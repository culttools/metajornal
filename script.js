/*
 * METAJORNAL — the news as the news
 *
 * Dual-mode frontend:
 *   LIVE  — /api/top, /api/stats, /api/sources (FastAPI)
 *   STATIC — data.json (GitHub Pages)
 */

const REFRESH_INTERVAL = 120_000;
let MODE = "unknown";
let autoTimer = null;
let cachedSources = null;

async function detectMode() {
    try {
        const r = await fetch("/api/top", { signal: AbortSignal.timeout(3000) });
        if (r.ok) { MODE = "live"; return; }
    } catch {}
    MODE = "static";
}

async function fetchData() {
    if (MODE === "live") {
        try {
            const r = await fetch("/api/top");
            if (!r.ok) throw new Error(r.status);
            const d = await r.json();
            return { stories: d.stories || [], stats: null };
        } catch { return null; }
    } else {
        try {
            const r = await fetch("data.json?_=" + Date.now());
            if (!r.ok) throw new Error(r.status);
            const d = await r.json();
            return {
                stories: d.stories || [],
                stats: d.stats || null,
                generated: d.generated_at,
                sources: d.sources || null,
            };
        } catch { return null; }
    }
}

async function fetchStats() {
    if (MODE !== "live") return null;
    try {
        const r = await fetch("/api/stats");
        return r.ok ? await r.json() : null;
    } catch { return null; }
}

async function fetchSources() {
    if (cachedSources) return cachedSources;
    if (MODE === "live") {
        try {
            const r = await fetch("/api/sources");
            if (r.ok) { cachedSources = await r.json(); return cachedSources; }
        } catch {}
    }
    try {
        const r = await fetch("sources.json?_=" + Date.now());
        if (r.ok) { cachedSources = await r.json(); return cachedSources; }
    } catch {}
    return null;
}

function esc(s) {
    if (!s) return "";
    return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function renderStories(stories) {
    const el = document.getElementById("news-list");
    if (!stories || !stories.length) {
        el.innerHTML = `<div class="empty">
            <span class="empty-icon">📡</span>NO STORIES YET<br>
            <small>Waiting for the next crawl cycle...</small></div>`;
        return;
    }

    el.innerHTML = stories.map((s, i) => {
        const isTop = i === 0;
        const main = s.articles[0];
        if (!main) return "";
        const others = s.articles.slice(1, 8);

        const badge = s.article_count > 1
            ? `<span class="c-badge">${s.article_count} SOURCES</span>` : "";

        const srcs = others.length
            ? `<div class="c-sources">${others.map(a =>
                `<a class="c-src" href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.source_name)}</a>`
              ).join('<span class="c-sep">|</span>')}</div>` : "";

        const lang = main.original_language !== "en" ? " [translated]" : "";
        const country = main.source_country ? ` (${esc(main.source_country)})` : "";

        return `<div class="cluster${isTop ? " top" : ""}">
            <span class="c-rank">#${i + 1}${badge}</span>
            <a class="c-headline" href="${esc(main.url)}" target="_blank" rel="noopener">${esc(s.headline)}</a>
            <div class="c-meta">${esc(main.source_name)}${country}${lang}</div>
            ${srcs}
        </div>`;
    }).join("");
}

async function renderSources() {
    const panel = document.getElementById("sources-panel");
    const data = await fetchSources();
    if (!data || !data.sources) {
        panel.innerHTML = `<div class="empty">Sources list not available in this mode.</div>`;
        return;
    }

    const byRegion = {};
    for (const s of data.sources) {
        const region = s.country || "INT";
        if (!byRegion[region]) byRegion[region] = [];
        byRegion[region].push(s);
    }

    const regionNames = {
        US: "UNITED STATES", UK: "UNITED KINGDOM", IE: "IRELAND",
        FR: "FRANCE", DE: "GERMANY", ES: "SPAIN", IT: "ITALY",
        PT: "PORTUGAL", NL: "NETHERLANDS", BE: "BELGIUM",
        NO: "NORWAY", SE: "SWEDEN", DK: "DENMARK", FI: "FINLAND",
        GR: "GREECE", AT: "AUSTRIA", CH: "SWITZERLAND",
        PL: "POLAND", CZ: "CZECHIA", HU: "HUNGARY", HR: "CROATIA",
        RS: "SERBIA", RO: "ROMANIA", BG: "BULGARIA",
        TR: "TURKEY", CA: "CANADA",
        AR: "ARGENTINA", BR: "BRAZIL", MX: "MEXICO", CL: "CHILE",
        CO: "COLOMBIA", EC: "ECUADOR", UY: "URUGUAY", PY: "PARAGUAY",
        CU: "CUBA", VE: "VENEZUELA",
        IN: "INDIA", PK: "PAKISTAN", NP: "NEPAL", BD: "BANGLADESH",
        TH: "THAILAND", PH: "PHILIPPINES", SG: "SINGAPORE",
        MY: "MALAYSIA", ID: "INDONESIA",
        KR: "SOUTH KOREA", JP: "JAPAN", HK: "HONG KONG", TW: "TAIWAN",
        QA: "QATAR", IL: "ISRAEL", LB: "LEBANON", EG: "EGYPT",
        TN: "TUNISIA", MA: "MOROCCO",
        ZA: "SOUTH AFRICA", KE: "KENYA", NG: "NIGERIA", ET: "ETHIOPIA",
        AU: "AUSTRALIA", NZ: "NEW ZEALAND",
        LV: "LATVIA (EXILE PRESS)", RU: "RUSSIA",
        INT: "INTERNATIONAL",
    };

    const order = Object.keys(regionNames);
    const sorted = Object.entries(byRegion).sort((a, b) => {
        const ia = order.indexOf(a[0]), ib = order.indexOf(b[0]);
        return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
    });

    let html = `<div class="source-count-banner">${data.sources.length} SOURCES ACROSS ${Object.keys(byRegion).length} COUNTRIES</div>`;
    html += `<div class="sources-grid">`;
    for (const [region, sources] of sorted) {
        const name = regionNames[region] || region;
        html += `<div class="source-region">`;
        html += `<div class="source-region-title">${esc(name)}</div>`;
        for (const s of sources) {
            const langTag = s.language !== "en" ? `<span class="source-lang">${esc(s.language)}</span>` : "";
            html += `<div class="source-entry"><a href="${esc(s.website)}" target="_blank" rel="noopener">${esc(s.name)}</a>${langTag}</div>`;
        }
        html += `</div>`;
    }
    html += `</div>`;
    panel.innerHTML = html;
}

function showTab(tab) {
    const newsEl = document.getElementById("news");
    const srcEl = document.getElementById("sources-panel");
    const tabNews = document.getElementById("tab-news");
    const tabSrc = document.getElementById("tab-sources");

    if (tab === "sources") {
        newsEl.style.display = "none";
        srcEl.classList.add("visible");
        tabNews.classList.remove("active");
        tabSrc.classList.add("active");
        renderSources();
    } else {
        newsEl.style.display = "block";
        srcEl.classList.remove("visible");
        tabNews.classList.add("active");
        tabSrc.classList.remove("active");
    }
}

function updateDateline() {
    document.getElementById("dateline").textContent = new Date()
        .toLocaleDateString("en-US", { weekday:"long", year:"numeric", month:"long", day:"numeric" })
        .toUpperCase();
}

function updateFooter(stats, generated) {
    if (stats) {
        document.getElementById("f-articles").textContent = `${stats.total_articles||0} ARTICLES`;
        document.getElementById("f-stories").textContent = `${stats.total_clusters||0} STORIES`;
        document.getElementById("stats-link").textContent =
            `${stats.total_articles||0} / ${stats.total_clusters||0}`;
    }
    if (generated) {
        const d = new Date(generated);
        document.getElementById("f-update").textContent =
            "GENERATED " + d.toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit"}) +
            " " + d.toLocaleDateString("en-US",{month:"short",day:"numeric"});
    } else {
        document.getElementById("f-update").textContent =
            "LIVE " + new Date().toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit"});
    }
}

async function refresh() {
    const data = await fetchData();
    if (!data) return;
    renderStories(data.stories);
    updateDateline();
    if (MODE === "live") {
        updateFooter(await fetchStats(), null);
    } else {
        updateFooter(data.stats, data.generated);
    }
}

async function manualRefresh() {
    const btn = document.getElementById("refresh-btn");
    if (MODE === "live") {
        try { await fetch("/api/crawl", { method: "POST" }); } catch {}
        btn.textContent = "CRAWLING...";
        setTimeout(() => { btn.textContent = "REFRESH"; refresh(); }, 15000);
    } else {
        btn.textContent = "LOADING...";
        await refresh();
        btn.textContent = "REFRESH";
    }
}

function showNews() {
    document.getElementById("loading").style.display = "none";
    document.getElementById("news").style.display = "block";
}

async function init() {
    updateDateline();
    await detectMode();
    const data = await fetchData();
    if (data && data.stories.length) {
        showNews();
        renderStories(data.stories);
        if (MODE === "live") updateFooter(await fetchStats(), null);
        else updateFooter(data.stats, data.generated);
    } else {
        setTimeout(async () => { showNews(); await refresh(); }, 8000);
    }
    autoTimer = setInterval(refresh, REFRESH_INTERVAL);
}

document.addEventListener("DOMContentLoaded", init);
