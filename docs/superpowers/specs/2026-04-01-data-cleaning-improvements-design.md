# Data Cleaning Improvements — Design Spec

**Date:** 2026-04-01
**Scope:** Enhancements to `clean_discographies.py`

## Overview

Improve the existing data cleaning pipeline with expanded title punctuation removal, composer/author/singer name standardization, data quality fixes, and a consolidation step that produces `discographies.csv`.

## 1. Title Cleaning

**Current behavior:** `TITLE_PUNCT = re.compile(r"[,\.!]")` removes commas, periods, and exclamation marks.

**New behavior:**
- Remove: `,` `.` `!` `?` `¿` `¡` `…` (unicode ellipsis U+2026)
- Remove three-dot sequences `...` including when embedded mid-word (e.g., `La C...ara de la L...una` → `La Cara de la Luna`)
- Preserve apostrophes (`'` and `'`)
- Collapse any resulting double-spaces

**Implementation:** Replace `TITLE_PUNCT` with a two-step approach:
1. First strip `...` (literal three-dot sequences) — this handles mid-word cases
2. Then strip individual punctuation characters: `[,\.!?¿¡…]`
3. Normalize whitespace

## 2. Name Field Standardization (Composer, Author, Singer)

### 2a. Add Singer to Name Fields

Add `"Singer"` to `NAME_FIELDS` so it receives the same token-level canonicalization pipeline as Composer and Author. This means Singer variants will be:
- Split by commas into tokens
- Canonicalized via `canonical_key()`
- Grouped and resolved to the best variant
- Included in `cleaning_report.json` variant groups

### 2b. Alias Resolution

Detect patterns where an alias is followed by a real name in parentheses and resolve to the real name:
- Pattern: `Alias (Real Name)` → `Real Name`
- Example: `Pancho Laguna (Francisco Lomuto)` → `Francisco Lomuto`

This runs in `clean_name_cell()` before other processing.

### 2c. Particle Capitalization (Argentine Convention)

Normalize lowercase particles to uppercase when they appear as the start of a surname segment (not at the very start of a full name token):
- `de` → `De`, `del` → `Del`, `di` → `Di`
- `los` → `Los`, `las` → `Las`, `la` → `La`
- Example: `Francisco de Caro` → `Francisco De Caro`

Applied after token canonicalization, as a post-processing step on each name token.

### 2d. Orphan Parenthesis Removal

Strip unmatched parentheses from name fields:
- `Avlis Cabrera)` → `Avlis Cabrera`
- Applied in `clean_name_cell()`

### 2e. Expanded Manual Canonical Overrides

Add known variants discovered during data audit to `MANUAL_CANONICAL_BY_KEY`. Specific entries to be determined during implementation by scanning the data for remaining inconsistencies after automated canonicalization.

## 3. Data Quality Fixes

- Orphan parenthesis stripping (covered in 2d)
- Ensure all unknown/placeholder variants (`unknown`, `unknwon`, `unnamed`, `n/a`, `?`, `[unknown]`, etc.) normalize to `UNKNOWN`
- Remove stray double-quotes left after name parsing
- Collapse double-spaces in all text fields after all transformations

## 4. Consolidation Step

After writing individual cleaned CSVs to `cleaned/`:

1. Read all cleaned CSVs back
2. Concatenate into a single dataset
3. Deduplicate across files using the same key: `(Orchestra, Title, Date, Singer)` — same logic as within-file dedup
4. Write to `discographies.csv` at the project root
5. Print total record count

## 5. Reporting Updates

- `cleaning_report.json` includes Singer variant groups alongside Composer and Author
- Console output prints Singer variant group count
- Console output prints total consolidated record count

## Non-Goals

- No refactoring of the existing script structure
- No external configuration files
- No changes to the audit scripts (`check_data_quality.py`, `thorough_data_audit.py`)
- No changes to column schema or output format beyond what's described
