# Tango Discography Viewer — Design Spec

**Date:** 2026-04-01
**Scope:** Single-page web app for browsing tango discographies, hosted on GitHub Pages, embedded in Google Sites.

## Overview

A single `index.html` file using vanilla JS and Papa Parse (CDN) that loads `discographies.csv`, lets users browse one bandleader at a time with filters, sorting, search, and CSV download.

## 1. Architecture

- **Single file:** `index.html` at repo root — HTML, CSS, and JS all inline. No build step.
- **Data source:** fetches `discographies.csv` (already in repo root, ~15,460 rows, 18 columns) on page load.
- **CSV parsing:** Papa Parse loaded from CDN (`https://cdnjs.cloudflare.com/ajax/libs/PapaParse/5.4.1/papaparse.min.js`).
- **Hosting:** GitHub Pages serves the repo root. Enable in repo Settings > Pages > Source: main branch, root.
- **Google Sites:** embed via `<iframe src="https://<username>.github.io/tango-discographies/">`.

**File structure change:** one new file (`index.html`). No other files modified.

## 2. Data Schema

Columns in `discographies.csv`:

| Column | Type | Notes |
|--------|------|-------|
| Bandleader | string | Primary selector |
| Orchestra | string | Detail panel |
| Date | date string (M/D/YYYY) | Table column, parsed for sorting |
| Title | string | Table column |
| AltTitle | string | Detail panel |
| Genre | string | Table column + filter dropdown |
| Singer | string | Table column + filter dropdown |
| Label | string | Table column |
| Master | string | Detail panel |
| Composer | string | Table column (secondary) |
| Author | string | Table column (secondary) |
| Arranger | string | Detail panel |
| Grouping | string | Table column + filter dropdown |
| Pianist | string | Detail panel |
| Bassist | string | Detail panel |
| Bandoneons | string | Detail panel |
| Strings | string | Detail panel |
| Lineup | string | Detail panel |

## 3. Layout & Styling

### Color Palette (from Notas de Oro)

- Background: `#faf8f4` (warm off-white)
- Header/controls background: `#fff`
- Gold accent: `#f1c232`
- Gold text (labels, headers): `#b8960a`
- Primary text: `#1c1c1c`
- Borders: `#e8e4dc`, `#d4cfc4`
- Table header background: `#f4f1eb`
- Selected row: `rgba(241,194,50,0.1)` background + `#f1c232` left border
- Detail panel background: `#f4f1eb`

### Typography

- Font stack: `Georgia, 'Times New Roman', serif`
- Title: 26px, weight 600
- Bandleader dropdown: 18px, weight 600
- Filter labels: 10px, uppercase, letter-spacing 1.5px, color `#b8960a`
- Table headers: 11px, uppercase, bold (700), letter-spacing 1px, color `#b8960a`
- Table body: 13px
- Stats line: 12px, color `rgba(0,0,0,0.35)`

### Page Sections (top to bottom)

1. **Header bar** — white background, gold bottom border (2px)
   - Left: title "Argentine Tango Discographies" + subtitle "15,460 recordings · 41 bandleaders"
   - Right: two outline buttons — "Download Artist CSV", "Download All CSV" (gold border, gold text)

2. **Bandleader selector** — white background
   - Gold uppercase label "BANDLEADER"
   - Large `<select>` dropdown (18px, bold). Each option shows "Artist Name · N recordings"

3. **Filters row** — off-white background
   - Four items inline: Genre dropdown, Singer dropdown, Grouping dropdown, Search input
   - "Clear filters" text link at the end
   - All dropdowns populated dynamically from the selected artist's data
   - All labels: gold uppercase

4. **Active filter chips** — shown below filters row only when filters are active
   - Small tags like "Genre: Vals ×" — click × to clear that filter

5. **Stats line** — "[N] recordings · [year range]" — updates with filters

6. **Data table** — scrollable container, sticky header
   - Columns: Date, Title, Genre, Singer, Label, Grouping, Composer, Author
   - Headers: bold, gold, uppercase, clickable for sort. Active sort column shows ▲/▼
   - Rows: subtle alternating shade (`rgba(0,0,0,0.015)`), hover highlight, cursor pointer
   - Selected row: gold tint background + 3px gold left inset shadow
   - "↓ scroll for more ↓" indicator at bottom (hidden when all rows fit without scrolling)

7. **Detail panel** — below table, gold left border (2px)
   - **Empty state:** gray left border, italic text "Select a row above to view full recording details"
   - **Populated:** shows Orchestra, Master, Arranger, Pianist, Bassist, Lineup, Bandoneons, Strings, AltTitle in a 3-column grid

8. **Back to top button** — floating, appears after scrolling down. Small, subtle, gold accent.

### Responsive Behavior

- On screens < 900px wide: hide Author column
- On screens < 750px wide: also hide Composer column
- On screens < 600px wide: also hide Grouping column
- Detail panel always shows all fields regardless of screen width
- Filter row wraps naturally via flexbox

## 4. Interactions & Data Flow

### Page Load
1. Show loading state
2. Fetch `discographies.csv` via `fetch()`
3. Parse with Papa Parse (`header: true, skipEmptyLines: true`)
4. Store full dataset in memory as array of objects
5. Extract sorted unique bandleader list, populate dropdown
6. Auto-select first bandleader alphabetically
7. Trigger bandleader change flow

### Bandleader Change
1. Filter dataset to rows matching selected bandleader
2. Extract unique values for Genre, Singer, Grouping from filtered set
3. Populate filter dropdowns (sorted, with "All" as first option)
4. Clear any active filters and search text
5. Reset sort to Date ascending
6. Clear row selection
7. Render table and update stats

### Filter / Search Change
1. Start from the artist's full recording set
2. Apply Genre filter if not "All"
3. Apply Singer filter if not "All"
4. Apply Grouping filter if not "All"
5. Apply search term (case-insensitive match against Title, Composer, Author, Singer)
6. Update active filter chips
7. Re-render table with filtered results
8. Update stats line
9. Clear row selection

### Search Debounce
- 300ms debounce on search input keystroke before triggering filter

### Column Sort
- Click header: sort ascending by that column
- Click same header again: toggle to descending
- Click different header: sort ascending by new column, clear previous
- Show ▲ (ascending) or ▼ (descending) on active sort column header
- Date column sorts chronologically (parse M/D/YYYY to Date objects)

### Row Selection
- Click row: highlight it, populate detail panel
- Click same row again: deselect, show empty detail state
- Click different row: move selection
- Escape key: clear selection

### Keyboard Navigation
- ↑/↓ arrow keys: move selection through visible rows
- Enter: toggle selection on focused row
- Escape: clear selection

### CSV Download (Artist)
- Generates CSV from currently displayed (filtered) rows
- All 18 columns included
- Filename: `{Bandleader Name}.csv`
- Uses Papa Parse `unparse()` or manual CSV generation

### CSV Download (All)
- Generates CSV from the full unfiltered dataset
- All 18 columns included
- Filename: `tango-discographies-all.csv`

## 5. Performance

- Full dataset loaded once into a JS array (~15K objects, ~3-5MB parsed)
- Filtering produces a new array (no DOM manipulation until render)
- Table renders all rows for the filtered artist set (largest: ~1,500 for Francisco Canaro) — no virtualization needed
- Search debounced at 300ms
- Sort uses native `Array.sort()` with appropriate comparators

## 6. Error Handling

- If CSV fetch fails: show centered error message "Could not load discography data. Please try refreshing."
- If CSV parses with 0 rows: show error message
- Empty filter results: show "No recordings match your filters" in the table area

## Non-Goals

- No server-side code or database
- No user authentication
- No editing or data entry
- No per-artist separate pages (single-page app with dropdown)
- No build tools or npm dependencies
- No changes to existing Python scripts or CSV files
