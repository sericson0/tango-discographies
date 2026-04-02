# Tango Discography Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-page discography viewer (`index.html`) that loads CSV data, lets users browse one bandleader at a time with filters, sorting, search, row detail, and CSV download.

**Architecture:** One `index.html` file at repo root with inline CSS and JS. Fetches `discographies.csv` on load, parses with Papa Parse (CDN), all logic client-side. Hosted via GitHub Pages, embedded in Google Sites via iframe.

**Tech Stack:** Vanilla HTML/CSS/JS, Papa Parse 5.4.1 (CDN)

**Spec:** `docs/superpowers/specs/2026-04-01-discography-viewer-design.md`

---

## File Structure

- **Create:** `index.html` (repo root) — the entire application (HTML structure, CSS styles, JS logic)

No other files created or modified.

---

## Task 1: HTML Structure + CSS Styling

Create `index.html` with the complete HTML skeleton and all CSS. No JS yet — just the static layout with placeholder content so the visual design can be verified.

**Files:**
- Create: `index.html`

- [ ] **Step 1: Create `index.html` with HTML structure and CSS**

Create `index.html` at the repo root with this content:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Argentine Tango Discographies</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/PapaParse/5.4.1/papaparse.min.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: Georgia, 'Times New Roman', serif;
      background: #faf8f4;
      color: #1c1c1c;
    }

    /* --- Header --- */
    .header {
      padding: 28px 32px 24px;
      border-bottom: 2px solid #f1c232;
      background: #fff;
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      flex-wrap: wrap;
      gap: 16px;
    }
    .header-title { font-size: 26px; font-weight: 600; color: #1c1c1c; letter-spacing: 0.5px; }
    .header-subtitle { font-size: 13px; color: rgba(0,0,0,0.4); margin-top: 6px; letter-spacing: 0.3px; }
    .header-actions { display: flex; gap: 10px; }
    .btn-download {
      border: 1px solid rgba(241,194,50,0.6);
      border-radius: 4px;
      padding: 7px 14px;
      font-size: 12px;
      color: #b8960a;
      cursor: pointer;
      letter-spacing: 0.3px;
      background: transparent;
      font-family: inherit;
      transition: background 0.15s;
    }
    .btn-download:hover { background: rgba(241,194,50,0.08); }

    /* --- Bandleader Selector --- */
    .bandleader-section {
      padding: 20px 32px;
      background: #fff;
      border-bottom: 1px solid #e8e4dc;
    }
    .field-label {
      font-size: 10px;
      text-transform: uppercase;
      color: #b8960a;
      margin-bottom: 6px;
      letter-spacing: 1.5px;
    }
    .bandleader-select {
      background: #faf8f4;
      border: 1px solid #d4cfc4;
      border-radius: 4px;
      padding: 12px 16px;
      font-size: 18px;
      color: #1c1c1c;
      font-weight: 600;
      font-family: inherit;
      width: 100%;
      cursor: pointer;
      appearance: none;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%23ccc' stroke-width='1.5' fill='none'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 16px center;
    }

    /* --- Filters Row --- */
    .filters-section {
      padding: 16px 32px;
      background: #faf8f4;
      border-bottom: 1px solid #e8e4dc;
    }
    .filters-row {
      display: flex;
      gap: 14px;
      align-items: flex-end;
      flex-wrap: wrap;
    }
    .filter-group { flex: 0 0 auto; }
    .filter-group.search { flex: 1 1 200px; }
    .filter-select, .filter-input {
      background: #fff;
      border: 1px solid #d4cfc4;
      border-radius: 4px;
      padding: 8px 12px;
      min-width: 110px;
      color: #1c1c1c;
      font-size: 13px;
      font-family: inherit;
      appearance: none;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%23ccc' stroke-width='1.5' fill='none'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 10px center;
    }
    .filter-input {
      background-image: none;
      min-width: 200px;
      width: 100%;
    }
    .filter-input::placeholder { color: rgba(0,0,0,0.3); }
    .clear-filters {
      padding: 8px 12px;
      font-size: 11px;
      color: rgba(0,0,0,0.3);
      cursor: pointer;
      background: none;
      border: none;
      font-family: inherit;
      align-self: flex-end;
      transition: color 0.15s;
    }
    .clear-filters:hover { color: #b8960a; }
    .clear-filters.has-filters { color: #b8960a; }

    /* --- Active Filter Chips --- */
    .chips-section {
      padding: 0 32px;
      background: #faf8f4;
      overflow: hidden;
      max-height: 0;
      transition: max-height 0.2s, padding 0.2s;
    }
    .chips-section.visible {
      padding: 10px 32px;
      max-height: 60px;
      border-bottom: 1px solid #e8e4dc;
    }
    .chips-row { display: flex; gap: 8px; flex-wrap: wrap; }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(241,194,50,0.12);
      border: 1px solid rgba(241,194,50,0.3);
      border-radius: 20px;
      padding: 4px 10px 4px 12px;
      font-size: 12px;
      color: #b8960a;
    }
    .chip-close {
      cursor: pointer;
      font-size: 14px;
      line-height: 1;
      color: #b8960a;
      opacity: 0.6;
      background: none;
      border: none;
      font-family: inherit;
      padding: 0;
    }
    .chip-close:hover { opacity: 1; }

    /* --- Stats Line --- */
    .stats-line {
      padding: 10px 32px;
      font-size: 12px;
      color: rgba(0,0,0,0.35);
      border-bottom: 1px solid #e8e4dc;
      letter-spacing: 0.2px;
    }

    /* --- Data Table --- */
    .table-container {
      max-height: 60vh;
      overflow-y: auto;
      position: relative;
    }
    .data-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    .data-table thead { position: sticky; top: 0; z-index: 1; }
    .data-table th {
      background: #f4f1eb;
      color: #b8960a;
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 1px;
      font-weight: 700;
      padding: 13px 10px 11px;
      text-align: left;
      border-bottom: 2px solid #d4cfc4;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }
    .data-table th:first-child { padding-left: 32px; }
    .data-table th .sort-arrow { font-size: 10px; margin-left: 4px; opacity: 0.4; }
    .data-table th.sorted .sort-arrow { opacity: 1; }
    .data-table td {
      padding: 10px 10px;
      border-bottom: 1px solid #ece8e0;
    }
    .data-table td:first-child { padding-left: 32px; }
    .data-table tbody tr { cursor: pointer; transition: background 0.1s; }
    .data-table tbody tr:nth-child(even) { background: rgba(0,0,0,0.015); }
    .data-table tbody tr:hover { background: rgba(241,194,50,0.05); }
    .data-table tbody tr.selected {
      background: rgba(241,194,50,0.1);
      box-shadow: inset 3px 0 0 #f1c232;
    }
    .data-table tbody tr.selected:hover { background: rgba(241,194,50,0.13); }
    .col-date { color: rgba(0,0,0,0.45); }
    .col-title { color: #1c1c1c; font-weight: 500; }
    .col-genre, .col-singer { color: rgba(0,0,0,0.6); }
    .col-label { color: rgba(0,0,0,0.5); }
    .col-grouping { color: rgba(0,0,0,0.4); font-size: 12px; }
    .col-composer { color: rgba(0,0,0,0.4); }
    .col-author { color: rgba(0,0,0,0.35); }

    /* Scroll indicator */
    .scroll-indicator {
      text-align: center;
      padding: 14px;
      color: rgba(0,0,0,0.2);
      font-size: 12px;
      letter-spacing: 0.5px;
      display: none;
    }
    .scroll-indicator.visible { display: block; }

    /* Empty / error states */
    .table-message {
      text-align: center;
      padding: 40px 32px;
      color: rgba(0,0,0,0.35);
      font-size: 14px;
      font-style: italic;
    }

    /* --- Detail Panel --- */
    .detail-panel {
      margin: 16px 32px 20px;
      background: #f4f1eb;
      padding: 16px 20px;
      border-left: 2px solid #e8e4dc;
      transition: border-color 0.15s;
    }
    .detail-panel.populated { border-left-color: #f1c232; }
    .detail-label {
      font-size: 10px;
      text-transform: uppercase;
      color: #b8960a;
      letter-spacing: 1.5px;
      margin-bottom: 12px;
    }
    .detail-empty {
      font-size: 14px;
      color: rgba(0,0,0,0.3);
      font-style: italic;
      text-align: center;
      padding: 8px 0;
    }
    .detail-grid {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 10px;
      font-size: 13px;
    }
    .detail-grid .detail-field-label { color: rgba(0,0,0,0.4); font-size: 12px; }
    .detail-grid .detail-field-value { color: #1c1c1c; margin-top: 2px; }
    .detail-grid .span2 { grid-column: span 2; }

    /* --- Back to Top Button --- */
    .back-to-top {
      position: fixed;
      bottom: 24px;
      right: 24px;
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: #fff;
      border: 1px solid #d4cfc4;
      color: #b8960a;
      font-size: 18px;
      cursor: pointer;
      display: none;
      align-items: center;
      justify-content: center;
      box-shadow: 0 2px 8px rgba(0,0,0,0.1);
      transition: background 0.15s;
      z-index: 10;
    }
    .back-to-top.visible { display: flex; }
    .back-to-top:hover { background: rgba(241,194,50,0.08); }

    /* --- Loading / Error --- */
    .loading-overlay {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 60vh;
      font-size: 16px;
      color: rgba(0,0,0,0.4);
    }
    .error-message {
      text-align: center;
      padding: 60px 32px;
      font-size: 16px;
      color: rgba(0,0,0,0.45);
    }

    /* --- Responsive --- */
    @media (max-width: 900px) {
      .col-author, .th-author { display: none; }
    }
    @media (max-width: 750px) {
      .col-composer, .th-composer { display: none; }
    }
    @media (max-width: 600px) {
      .col-grouping, .th-grouping { display: none; }
      .header { padding: 20px 16px 16px; }
      .bandleader-section, .filters-section, .stats-line { padding-left: 16px; padding-right: 16px; }
      .chips-section, .chips-section.visible { padding-left: 16px; padding-right: 16px; }
      .data-table th:first-child, .data-table td:first-child { padding-left: 16px; }
      .detail-panel { margin-left: 16px; margin-right: 16px; }
      .detail-grid { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>

  <!-- Loading state -->
  <div id="loading" class="loading-overlay">Loading discographies...</div>

  <!-- Error state (hidden by default) -->
  <div id="error" class="error-message" style="display:none;"></div>

  <!-- Main app (hidden until data loads) -->
  <div id="app" style="display:none;">

    <!-- Header -->
    <div class="header">
      <div>
        <div class="header-title">Argentine Tango Discographies</div>
        <div class="header-subtitle" id="headerSubtitle"></div>
      </div>
      <div class="header-actions">
        <button class="btn-download" id="btnDownloadArtist">Download Artist CSV</button>
        <button class="btn-download" id="btnDownloadAll">Download All CSV</button>
      </div>
    </div>

    <!-- Bandleader Selector -->
    <div class="bandleader-section">
      <div class="field-label">Bandleader</div>
      <select id="bandleaderSelect" class="bandleader-select"></select>
    </div>

    <!-- Filters Row -->
    <div class="filters-section">
      <div class="filters-row">
        <div class="filter-group">
          <div class="field-label">Genre</div>
          <select id="genreFilter" class="filter-select"></select>
        </div>
        <div class="filter-group">
          <div class="field-label">Singer</div>
          <select id="singerFilter" class="filter-select" style="min-width:160px;"></select>
        </div>
        <div class="filter-group">
          <div class="field-label">Grouping</div>
          <select id="groupingFilter" class="filter-select" style="min-width:180px;"></select>
        </div>
        <div class="filter-group search">
          <div class="field-label">Search</div>
          <input id="searchInput" class="filter-input" type="text" placeholder="Search titles, composers, authors...">
        </div>
        <button id="clearFilters" class="clear-filters">Clear filters</button>
      </div>
    </div>

    <!-- Active Filter Chips -->
    <div id="chipsSection" class="chips-section">
      <div id="chipsRow" class="chips-row"></div>
    </div>

    <!-- Stats Line -->
    <div class="stats-line" id="statsLine"></div>

    <!-- Data Table -->
    <div class="table-container" id="tableContainer">
      <table class="data-table">
        <thead>
          <tr>
            <th data-col="Date">Date <span class="sort-arrow">&#9650;</span></th>
            <th data-col="Title">Title <span class="sort-arrow">&#9650;</span></th>
            <th data-col="Genre">Genre <span class="sort-arrow">&#9650;</span></th>
            <th data-col="Singer">Singer <span class="sort-arrow">&#9650;</span></th>
            <th data-col="Label">Label <span class="sort-arrow">&#9650;</span></th>
            <th data-col="Grouping" class="th-grouping">Grouping <span class="sort-arrow">&#9650;</span></th>
            <th data-col="Composer" class="th-composer">Composer <span class="sort-arrow">&#9650;</span></th>
            <th data-col="Author" class="th-author">Author <span class="sort-arrow">&#9650;</span></th>
          </tr>
        </thead>
        <tbody id="tableBody"></tbody>
      </table>
      <div id="scrollIndicator" class="scroll-indicator">&#8595; scroll for more &#8595;</div>
    </div>

    <!-- Detail Panel -->
    <div id="detailPanel" class="detail-panel">
      <div class="detail-label" id="detailLabel">Recording Detail</div>
      <div id="detailContent">
        <div class="detail-empty">Select a row above to view full recording details</div>
      </div>
    </div>

  </div>

  <!-- Back to Top -->
  <button id="backToTop" class="back-to-top" title="Back to top">&#9650;</button>

  <script>
    // JS will be added in subsequent tasks
  </script>
</body>
</html>
```

- [ ] **Step 2: Open in browser to verify layout**

Open `index.html` in a browser. You should see the loading state ("Loading discographies..."). The CSS is complete — all subsequent tasks add only JS inside the `<script>` tag.

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add index.html with complete HTML structure and CSS styling"
```

---

## Task 2: Data Loading + Bandleader Dropdown

Add JS to fetch and parse the CSV, populate the bandleader dropdown, and wire up the initial data flow. After this task, the dropdown works and switching bandleaders updates state.

**Files:**
- Modify: `index.html` (replace the `<script>` block)

- [ ] **Step 1: Add data loading and bandleader logic**

Replace the `<script>` block in `index.html` (the one containing `// JS will be added in subsequent tasks`) with:

```javascript
(function() {
  'use strict';

  // --- State ---
  let allData = [];
  let artistData = [];
  let filteredData = [];
  let selectedRowIndex = -1;
  let sortCol = 'Date';
  let sortAsc = true;

  // --- DOM refs ---
  const $ = id => document.getElementById(id);
  const loadingEl = $('loading');
  const errorEl = $('error');
  const appEl = $('app');
  const headerSubtitle = $('headerSubtitle');
  const bandleaderSelect = $('bandleaderSelect');
  const genreFilter = $('genreFilter');
  const singerFilter = $('singerFilter');
  const groupingFilter = $('groupingFilter');
  const searchInput = $('searchInput');
  const clearFiltersBtn = $('clearFilters');
  const chipsSection = $('chipsSection');
  const chipsRow = $('chipsRow');
  const statsLine = $('statsLine');
  const tableBody = $('tableBody');
  const tableContainer = $('tableContainer');
  const scrollIndicator = $('scrollIndicator');
  const detailPanel = $('detailPanel');
  const detailLabel = $('detailLabel');
  const detailContent = $('detailContent');
  const backToTopBtn = $('backToTop');
  const btnDownloadArtist = $('btnDownloadArtist');
  const btnDownloadAll = $('btnDownloadAll');

  // --- Date parsing ---
  function parseDate(str) {
    if (!str) return null;
    const parts = str.split('/');
    if (parts.length !== 3) return null;
    return new Date(+parts[2], +parts[0] - 1, +parts[1]);
  }

  function formatDate(str) {
    const d = parseDate(str);
    if (!d) return str || '';
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  }

  // --- Unique sorted values ---
  function uniqueSorted(arr, key) {
    const vals = new Set();
    arr.forEach(r => { if (r[key] && r[key].trim()) vals.add(r[key].trim()); });
    return Array.from(vals).sort((a, b) => a.localeCompare(b));
  }

  // --- Populate a <select> with options ---
  function populateSelect(sel, values, allLabel) {
    sel.innerHTML = '';
    const allOpt = document.createElement('option');
    allOpt.value = '';
    allOpt.textContent = allLabel || 'All';
    sel.appendChild(allOpt);
    values.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v;
      opt.textContent = v;
      sel.appendChild(opt);
    });
  }

  // --- Bandleader dropdown ---
  function populateBandleaderDropdown() {
    const counts = {};
    allData.forEach(r => {
      const bl = r.Bandleader;
      counts[bl] = (counts[bl] || 0) + 1;
    });
    const names = Object.keys(counts).sort((a, b) => a.localeCompare(b));
    bandleaderSelect.innerHTML = '';
    names.forEach(name => {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name + ' \u00B7 ' + counts[name] + ' rec.';
      bandleaderSelect.appendChild(opt);
    });
    headerSubtitle.textContent = allData.length.toLocaleString() + ' recordings \u00B7 ' + names.length + ' bandleaders';
  }

  // --- Bandleader change ---
  function onBandleaderChange() {
    const bl = bandleaderSelect.value;
    artistData = allData.filter(r => r.Bandleader === bl);

    // Populate filter dropdowns for this artist
    populateSelect(genreFilter, uniqueSorted(artistData, 'Genre'), 'All');
    populateSelect(singerFilter, uniqueSorted(artistData, 'Singer'), 'All');
    populateSelect(groupingFilter, uniqueSorted(artistData, 'Grouping'), 'All');

    // Reset filters and search
    searchInput.value = '';
    sortCol = 'Date';
    sortAsc = true;
    selectedRowIndex = -1;

    applyFilters();
  }

  // --- PLACEHOLDER for tasks 3-6 ---
  function applyFilters() {
    // Will be implemented in Task 3
    filteredData = artistData.slice();
    sortData();
    renderTable();
    updateStats();
    updateChips();
    clearSelection();
  }

  function sortData() {
    // Will be fully implemented in Task 4
    const col = sortCol;
    const asc = sortAsc;
    filteredData.sort((a, b) => {
      let va, vb;
      if (col === 'Date') {
        va = parseDate(a.Date);
        vb = parseDate(b.Date);
        if (!va && !vb) return 0;
        if (!va) return 1;
        if (!vb) return -1;
        return asc ? va - vb : vb - va;
      }
      va = (a[col] || '').toLowerCase();
      vb = (b[col] || '').toLowerCase();
      const cmp = va.localeCompare(vb);
      return asc ? cmp : -cmp;
    });
  }

  function renderTable() {
    // Will be fully implemented in Task 3
    tableBody.innerHTML = '';
    if (filteredData.length === 0) {
      tableBody.innerHTML = '<tr><td colspan="8" class="table-message">No recordings match your filters</td></tr>';
      scrollIndicator.classList.remove('visible');
      return;
    }
    filteredData.forEach((row, i) => {
      const tr = document.createElement('tr');
      if (i === selectedRowIndex) tr.classList.add('selected');
      tr.innerHTML =
        '<td class="col-date">' + esc(formatDate(row.Date)) + '</td>' +
        '<td class="col-title">' + esc(row.Title) + '</td>' +
        '<td class="col-genre">' + esc(row.Genre) + '</td>' +
        '<td class="col-singer">' + esc(row.Singer) + '</td>' +
        '<td class="col-label">' + esc(row.Label) + '</td>' +
        '<td class="col-grouping">' + esc(row.Grouping) + '</td>' +
        '<td class="col-composer">' + esc(row.Composer) + '</td>' +
        '<td class="col-author">' + esc(row.Author) + '</td>';
      tr.addEventListener('click', () => onRowClick(i));
      tableBody.appendChild(tr);
    });
    checkScrollIndicator();
  }

  function esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function updateStats() {
    // Will be refined in Task 3
    if (filteredData.length === 0) {
      statsLine.textContent = '0 recordings';
      return;
    }
    const dates = filteredData.map(r => parseDate(r.Date)).filter(Boolean).sort((a, b) => a - b);
    const minYear = dates.length ? dates[0].getFullYear() : '?';
    const maxYear = dates.length ? dates[dates.length - 1].getFullYear() : '?';
    statsLine.textContent = filteredData.length + ' recordings \u00B7 ' + minYear + '\u2013' + maxYear;
  }

  function updateChips() {
    // Will be implemented in Task 5
    chipsRow.innerHTML = '';
    chipsSection.classList.remove('visible');
  }

  function clearSelection() {
    selectedRowIndex = -1;
    detailPanel.classList.remove('populated');
    detailLabel.textContent = 'Recording Detail';
    detailContent.innerHTML = '<div class="detail-empty">Select a row above to view full recording details</div>';
    const prev = tableBody.querySelector('tr.selected');
    if (prev) prev.classList.remove('selected');
  }

  function onRowClick(index) {
    // Will be fully implemented in Task 4
    if (selectedRowIndex === index) {
      clearSelection();
      return;
    }
    selectedRowIndex = index;
    const rows = tableBody.querySelectorAll('tr');
    rows.forEach((r, i) => r.classList.toggle('selected', i === index));
    showDetail(filteredData[index]);
  }

  function showDetail(row) {
    // Will be fully implemented in Task 4
    if (!row) { clearSelection(); return; }
    detailPanel.classList.add('populated');
    detailLabel.textContent = 'Recording Detail \u00B7 ' + (row.Title || '') + (row.Date ? ' (' + parseDate(row.Date)?.getFullYear() + ')' : '');
    detailContent.innerHTML = '';
  }

  function checkScrollIndicator() {
    const el = tableContainer;
    if (el.scrollHeight > el.clientHeight + 20) {
      scrollIndicator.classList.add('visible');
    } else {
      scrollIndicator.classList.remove('visible');
    }
  }

  // --- Init ---
  function init() {
    Papa.parse('discographies.csv', {
      download: true,
      header: true,
      skipEmptyLines: true,
      complete: function(results) {
        if (!results.data || results.data.length === 0) {
          loadingEl.style.display = 'none';
          errorEl.style.display = 'block';
          errorEl.textContent = 'Could not load discography data. Please try refreshing.';
          return;
        }
        allData = results.data;
        populateBandleaderDropdown();
        loadingEl.style.display = 'none';
        appEl.style.display = 'block';

        // Wire up bandleader change
        bandleaderSelect.addEventListener('change', onBandleaderChange);

        // Trigger initial load
        onBandleaderChange();
      },
      error: function() {
        loadingEl.style.display = 'none';
        errorEl.style.display = 'block';
        errorEl.textContent = 'Could not load discography data. Please try refreshing.';
      }
    });
  }

  init();
})();
```

- [ ] **Step 2: Test in browser**

Open `index.html` via a local web server (required for `fetch`):

```bash
cd c:/Users/seric/OneDrive/Documents/GitHub/tango-discographies
python -m http.server 8000
```

Open `http://localhost:8000`. Expected:
- Loading message appears briefly, then the app shows
- Bandleader dropdown populated with all 41 artists (alphabetical, with recording counts)
- Switching bandleaders updates the filter dropdowns
- Table shows rows for the selected artist with dates, titles, genres

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add data loading, bandleader dropdown, and basic table rendering"
```

---

## Task 3: Filtering, Search, and Stats

Wire up the Genre/Singer/Grouping filters, search with debounce, Clear filters button, and dynamic stats line.

**Files:**
- Modify: `index.html` (update functions inside `<script>`)

- [ ] **Step 1: Replace `applyFilters` and add filter event listeners**

In `index.html`, find the `applyFilters` function and replace it entirely with:

```javascript
  function applyFilters() {
    const genre = genreFilter.value;
    const singer = singerFilter.value;
    const grouping = groupingFilter.value;
    const search = searchInput.value.trim().toLowerCase();

    filteredData = artistData.filter(r => {
      if (genre && r.Genre !== genre) return false;
      if (singer && r.Singer !== singer) return false;
      if (grouping && r.Grouping !== grouping) return false;
      if (search) {
        const hay = ((r.Title || '') + ' ' + (r.Composer || '') + ' ' + (r.Author || '') + ' ' + (r.Singer || '')).toLowerCase();
        if (!hay.includes(search)) return false;
      }
      return true;
    });

    sortData();
    renderTable();
    updateStats();
    updateChips();
    clearSelection();

    // Update clear-filters button state
    const hasFilters = genre || singer || grouping || search;
    clearFiltersBtn.classList.toggle('has-filters', !!hasFilters);
  }
```

- [ ] **Step 2: Add filter event listeners after the `bandleaderSelect` listener in `init()`**

Find the line `bandleaderSelect.addEventListener('change', onBandleaderChange);` and add these lines immediately after it:

```javascript
        genreFilter.addEventListener('change', applyFilters);
        singerFilter.addEventListener('change', applyFilters);
        groupingFilter.addEventListener('change', applyFilters);

        // Search with debounce
        let searchTimeout;
        searchInput.addEventListener('input', () => {
          clearTimeout(searchTimeout);
          searchTimeout = setTimeout(applyFilters, 300);
        });

        // Clear filters
        clearFiltersBtn.addEventListener('click', () => {
          genreFilter.value = '';
          singerFilter.value = '';
          groupingFilter.value = '';
          searchInput.value = '';
          applyFilters();
        });
```

- [ ] **Step 3: Test in browser**

At `http://localhost:8000`:
- Select a bandleader with multiple genres (e.g., Anibal Troilo)
- Filter by Genre "Vals" — table should show only Vals recordings, stats update
- Filter by Singer — further narrows results
- Type in search "malena" — should show matching titles
- Click "Clear filters" — resets all, full artist list returns
- Stats line shows correct count and year range

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat: add genre/singer/grouping filters, search with debounce, clear filters"
```

---

## Task 4: Sorting, Row Selection, and Detail Panel

Wire up column header sorting with direction indicators, row click selection, detail panel population, and keyboard navigation.

**Files:**
- Modify: `index.html` (update functions + add event listeners inside `<script>`)

- [ ] **Step 1: Update sort header indicators**

Find the existing `sortData` function — it is already correct. Now add the visual sort indicators. Add this new function right after `sortData`:

```javascript
  function updateSortHeaders() {
    document.querySelectorAll('.data-table th').forEach(th => {
      const col = th.getAttribute('data-col');
      const arrow = th.querySelector('.sort-arrow');
      if (col === sortCol) {
        th.classList.add('sorted');
        arrow.innerHTML = sortAsc ? '&#9650;' : '&#9660;';
      } else {
        th.classList.remove('sorted');
        arrow.innerHTML = '&#9650;';
      }
    });
  }
```

Then update `applyFilters` — add `updateSortHeaders();` right after the `sortData();` call:

```javascript
    sortData();
    updateSortHeaders();
    renderTable();
```

- [ ] **Step 2: Add column header click listeners**

Add this after the `clearFiltersBtn` listener in `init()`:

```javascript
        // Column sort
        document.querySelectorAll('.data-table th').forEach(th => {
          th.addEventListener('click', () => {
            const col = th.getAttribute('data-col');
            if (col === sortCol) {
              sortAsc = !sortAsc;
            } else {
              sortCol = col;
              sortAsc = true;
            }
            sortData();
            updateSortHeaders();
            renderTable();
          });
        });
```

- [ ] **Step 3: Replace `showDetail` with full implementation**

Replace the existing `showDetail` function:

```javascript
  function showDetail(row) {
    if (!row) { clearSelection(); return; }
    detailPanel.classList.add('populated');
    const year = parseDate(row.Date) ? parseDate(row.Date).getFullYear() : '';
    detailLabel.textContent = 'Recording Detail \u00B7 ' + (row.Title || '') + (year ? ' (' + year + ')' : '');

    const fields = [
      { label: 'Orchestra', value: row.Orchestra },
      { label: 'Master', value: row.Master },
      { label: 'Arranger', value: row.Arranger },
      { label: 'Pianist', value: row.Pianist },
      { label: 'Bassist', value: row.Bassist },
      { label: 'Lineup', value: row.Lineup },
      { label: 'Bandoneons', value: row.Bandoneons, span2: true },
      { label: 'Strings', value: row.Strings },
    ];
    if (row.AltTitle) {
      fields.unshift({ label: 'Alt. Title', value: row.AltTitle });
    }

    const grid = document.createElement('div');
    grid.className = 'detail-grid';
    fields.forEach(f => {
      const div = document.createElement('div');
      if (f.span2) div.className = 'span2';
      div.innerHTML = '<div class="detail-field-label">' + esc(f.label) + '</div>' +
                      '<div class="detail-field-value">' + esc(f.value || '\u2014') + '</div>';
      grid.appendChild(div);
    });
    detailContent.innerHTML = '';
    detailContent.appendChild(grid);
  }
```

- [ ] **Step 4: Add keyboard navigation**

Add this after the column sort listeners in `init()`:

```javascript
        // Keyboard navigation
        document.addEventListener('keydown', (e) => {
          if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
          if (filteredData.length === 0) return;

          if (e.key === 'ArrowDown') {
            e.preventDefault();
            const next = selectedRowIndex < filteredData.length - 1 ? selectedRowIndex + 1 : 0;
            onRowClick(next);
            scrollToRow(next);
          } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            const prev = selectedRowIndex > 0 ? selectedRowIndex - 1 : filteredData.length - 1;
            onRowClick(prev);
            scrollToRow(prev);
          } else if (e.key === 'Enter' && selectedRowIndex >= 0) {
            e.preventDefault();
            onRowClick(selectedRowIndex);
          } else if (e.key === 'Escape') {
            clearSelection();
          }
        });
```

And add this helper function near the other helpers:

```javascript
  function scrollToRow(index) {
    const rows = tableBody.querySelectorAll('tr');
    if (rows[index]) {
      rows[index].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }
```

- [ ] **Step 5: Test in browser**

At `http://localhost:8000`:
- Click "Title" header — table sorts by title A-Z, arrow shows ▲
- Click "Title" again — sorts Z-A, arrow shows ▼
- Click "Date" — sorts by date ascending
- Click a row — row highlights gold, detail panel populates with Orchestra, Master, Pianist, etc.
- Click same row again — deselects, detail shows empty message
- Use ↑/↓ arrow keys — moves through rows
- Press Escape — clears selection

- [ ] **Step 6: Commit**

```bash
git add index.html
git commit -m "feat: add column sorting, row selection with detail panel, keyboard navigation"
```

---

## Task 5: Filter Chips, Scroll Indicator, and Back-to-Top

Wire up active filter chips with dismiss, the scroll-for-more indicator, and the back-to-top floating button.

**Files:**
- Modify: `index.html` (update functions + add event listeners inside `<script>`)

- [ ] **Step 1: Replace `updateChips` with full implementation**

Replace the existing `updateChips` function:

```javascript
  function updateChips() {
    chipsRow.innerHTML = '';
    const filters = [];
    if (genreFilter.value) filters.push({ label: 'Genre', value: genreFilter.value, clear: () => { genreFilter.value = ''; } });
    if (singerFilter.value) filters.push({ label: 'Singer', value: singerFilter.value, clear: () => { singerFilter.value = ''; } });
    if (groupingFilter.value) filters.push({ label: 'Grouping', value: groupingFilter.value, clear: () => { groupingFilter.value = ''; } });
    if (searchInput.value.trim()) filters.push({ label: 'Search', value: searchInput.value.trim(), clear: () => { searchInput.value = ''; } });

    if (filters.length === 0) {
      chipsSection.classList.remove('visible');
      return;
    }
    chipsSection.classList.add('visible');
    filters.forEach(f => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.innerHTML = esc(f.label) + ': ' + esc(f.value) + ' ';
      const closeBtn = document.createElement('button');
      closeBtn.className = 'chip-close';
      closeBtn.innerHTML = '&times;';
      closeBtn.addEventListener('click', () => { f.clear(); applyFilters(); });
      chip.appendChild(closeBtn);
      chipsRow.appendChild(chip);
    });
  }
```

- [ ] **Step 2: Add scroll indicator and back-to-top listeners**

Add this after the keyboard listener in `init()`:

```javascript
        // Scroll indicator + back to top
        tableContainer.addEventListener('scroll', () => {
          const el = tableContainer;
          const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
          if (nearBottom) {
            scrollIndicator.classList.remove('visible');
          }
        });

        window.addEventListener('scroll', () => {
          backToTopBtn.classList.toggle('visible', window.scrollY > 300);
        });

        backToTopBtn.addEventListener('click', () => {
          window.scrollTo({ top: 0, behavior: 'smooth' });
        });
```

- [ ] **Step 3: Test in browser**

At `http://localhost:8000`:
- Select Genre "Tango" — chip appears "Genre: Tango ×"
- Add Singer filter — second chip appears
- Click × on Genre chip — Genre clears, only Singer chip remains
- Select an artist with many recordings (Francisco Canaro) — scroll indicator shows
- Scroll to bottom of table — indicator hides
- Scroll the page down — back-to-top button appears
- Click it — scrolls to top

- [ ] **Step 4: Commit**

```bash
git add index.html
git commit -m "feat: add filter chips, scroll indicator, and back-to-top button"
```

---

## Task 6: CSV Download

Wire up both download buttons — "Download Artist CSV" (filtered rows) and "Download All CSV" (full dataset).

**Files:**
- Modify: `index.html` (add function + event listeners inside `<script>`)

- [ ] **Step 1: Add CSV download function and wire up buttons**

Add this function near the other helpers:

```javascript
  function downloadCSV(data, filename) {
    const csv = Papa.unparse(data);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }
```

Add these after the `backToTopBtn` listener in `init()`:

```javascript
        // CSV downloads
        btnDownloadArtist.addEventListener('click', () => {
          const name = bandleaderSelect.value || 'artist';
          downloadCSV(filteredData, name + '.csv');
        });

        btnDownloadAll.addEventListener('click', () => {
          downloadCSV(allData, 'tango-discographies-all.csv');
        });
```

- [ ] **Step 2: Test in browser**

At `http://localhost:8000`:
- Select Anibal Troilo, click "Download Artist CSV" — downloads `Anibal Troilo.csv` (should contain the filtered rows for that artist)
- Apply a filter (e.g., Genre: Vals), click "Download Artist CSV" — downloads only the filtered rows
- Click "Download All CSV" — downloads `tango-discographies-all.csv` with all ~15K rows
- Open downloaded files in a spreadsheet to verify they have all 18 columns

- [ ] **Step 3: Commit**

```bash
git add index.html
git commit -m "feat: add CSV download for current artist and all artists"
```

---

## Task 7: Final Polish and Verification

End-to-end verification of all features, fix any edge cases, and final commit.

**Files:**
- Modify: `index.html` (if any fixes needed)

- [ ] **Step 1: Full manual test checklist**

Run through this checklist at `http://localhost:8000`:

1. Page loads, shows loading briefly, then full app
2. Bandleader dropdown shows 41 artists with recording counts
3. Switching artists updates filter dropdowns, clears filters, resets sort
4. Genre/Singer/Grouping filters narrow results correctly
5. Filters combine with AND logic
6. Search works across Title, Composer, Author, Singer (300ms debounce)
7. Active filter chips appear and dismiss correctly
8. Clear filters button resets everything
9. Column headers sort ascending/descending with arrow indicators
10. Date sorts chronologically, not alphabetically
11. Clicking a row highlights it and shows detail panel
12. Detail panel shows all fields: Orchestra, Master, Arranger, Pianist, Bassist, Lineup, Bandoneons, Strings, AltTitle
13. Clicking selected row deselects it, shows empty state message
14. Arrow keys navigate rows, Escape clears
15. Download Artist CSV downloads filtered data
16. Download All CSV downloads all data
17. Scroll indicator appears/hides appropriately
18. Back-to-top button appears when scrolling down
19. Responsive: resize to < 900px (Author hides), < 750px (Composer hides), < 600px (Grouping hides)
20. Empty filter results show "No recordings match your filters"

- [ ] **Step 2: Fix any issues found**

Apply any fixes needed based on the test checklist.

- [ ] **Step 3: Final commit**

```bash
git add index.html
git commit -m "feat: complete tango discography viewer with all features"
```

---

## Summary

| Task | What it delivers | Commit |
|------|-----------------|--------|
| 1 | HTML + CSS skeleton (visual design complete) | `feat: add index.html with complete HTML structure and CSS styling` |
| 2 | Data loading, bandleader dropdown, basic table | `feat: add data loading, bandleader dropdown, and basic table rendering` |
| 3 | Filtering, search, stats line | `feat: add genre/singer/grouping filters, search with debounce, clear filters` |
| 4 | Column sorting, row selection, detail panel, keyboard nav | `feat: add column sorting, row selection with detail panel, keyboard navigation` |
| 5 | Filter chips, scroll indicator, back-to-top | `feat: add filter chips, scroll indicator, and back-to-top button` |
| 6 | CSV download (artist + all) | `feat: add CSV download for current artist and all artists` |
| 7 | Final verification and polish | `feat: complete tango discography viewer with all features` |
