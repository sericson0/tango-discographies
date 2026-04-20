# Tango Discographies

A searchable web viewer for tango orchestra discographies.

**Live site:** https://sericson0.github.io/tango-discographies/

<!-- TODO: add screenshot.png here once captured -->

## Features

- Browse discographies of ~40 classic tango orchestras and singers
- Full-text search across titles, composers, authors, and singers
- Filter by genre, singer, and grouping
- Sort any column; click a row for a detail popup
- Download per-artist or full-catalog CSV
- Keyboard navigation

## Data sources

This dataset is a mix of original compilation and data derived from public
tango resources. Entries may contain errors, omissions, or transcription
inconsistencies — corrections and additions are warmly welcomed. See
[CONTRIBUTING.md](CONTRIBUTING.md) to help improve the data.

## Contributing

Three ways to contribute:

- **Spotted a data error?** Open a [Data Correction issue](../../issues/new?template=data_correction.yml).
- **Found a bug or have a feature idea?** Open a [bug report](../../issues/new?template=bug_report.yml) or [feature request](../../issues/new?template=feature_request.yml).
- **Want to submit code or data directly?** Open a pull request. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

Questions? Start a [Discussion](../../discussions) or email [TangoToolkit@gmail.com](mailto:TangoToolkit@gmail.com).

## Development

**Requirements:** Python 3.10+ (standard library only — no dependencies) and any modern browser.

```bash
# Regenerate the compiled CSV from per-artist files
python build.py

# Serve locally (required so the browser can fetch discographies.csv)
python -m http.server 8000
# then open http://localhost:8000
```

**Project layout:**

```
csv_files/          per-artist source-of-truth CSVs
build.py            compiles csv_files/*.csv -> discographies.csv
check_data_quality.py    data-quality linter
thorough_data_audit.py   deeper data audit
index.html          the viewer (plain HTML/CSS/JS, no build step)
```

## License

[MIT](LICENSE) — (c) 2026 Sean Ericson

## Citation

If you reference this project in research or a publication, please cite:

> Ericson, S. (2026). *Tango Discographies* [Data set and software]. Retrieved from https://github.com/sericson0/tango-discographies

<details>
<summary>Maintainer setup checklist</summary>

One-time GitHub UI actions:

- Enable **Discussions** (Settings -> Features -> Discussions)
- Add an "About" description and the live-site URL
- Add repo topics: `tango`, `discography`, `music`, `dataset`, `argentine-tango`
- Optional: branch protection on `main` (require PR reviews, require status checks)

</details>
