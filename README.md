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

## Image sources

The record-label and album-cover images shown in the viewer come from the
collectors, archives and catalogues below. Where the source of an individual
image is on record, the viewer credits it under that image in the detail popup
and the full-size view (see [`image_credits.txt`](#image-credits-image_creditstxt)).
The images remain the property of their respective owners and are shown for
discographic reference; this project claims no licence for them, and the MIT
licence below covers the code and the discography data only.

- **José Manuel Araque — GuardiaVieja.org**: 78 rpm label scans from his
  discography blogs for Fresedo, Pizarro, Cobián, De Caro and Maffia–Laurenz
  ([fresedo.de](https://www.fresedo.de/),
  [tangodiscography.blogspot.com](https://tangodiscography.blogspot.com/)).
  Each credited image links to the blog post it comes from.
- **Discography of American Historical Recordings (DAHR)**, UC Santa Barbara
  Library ([adp.library.ucsb.edu](https://adp.library.ucsb.edu/)). Each credited
  image links to the DAHR matrix page of that recording. DAHR's own citation
  form, for example:
  > Discography of American Historical Recordings, s.v. "Victor matrix
  > BAVE-012762. Arrabalero / Orquesta Típica Osvaldo Fresedo," accessed
  > May 10, 2026, https://adp.library.ucsb.edu/index.php/matrix/detail/2000441980/BAVE-012762-Arrabalero.

  DAHR notes that its information on many of these recordings derives from
  data compiled and provided by Enrique Binda, as well as disc labels examined
  by DAHR editors.
- **tango.info** ([tango.info](https://tango.info/))
- **Discogs** ([discogs.com](https://www.discogs.com/)), for most LP, EP and CD
  covers
- **tangos78rpm.com** ([tangos78rpm.com](https://www.tangos78rpm.com/))
- **Internet Archive**, Great 78 Project
  ([great78.archive.org](https://great78.archive.org/))
- **45cat** ([45cat.com](https://www.45cat.com/)) and **astorpiazzolla.com**
  ([astorpiazzolla.com](https://astorpiazzolla.com/))
- Listing photographs from **eBay**, **Mercado Libre** and **popsike**
- Album covers from **Apple Music / iTunes**, **Bandcamp**, **Deezer** and the
  artists' official sites

If you hold the rights to an image and would like its credit corrected or the
image removed, please write to
[TangoToolkit@gmail.com](mailto:TangoToolkit@gmail.com).

## Contributing

Three ways to contribute:

- **Spotted a data error?** Open a [Data Correction issue](../../issues/new?template=data_correction.yml).
- **Found a bug or have a feature idea?** Open a [bug report](../../issues/new?template=bug_report.yml) or [feature request](../../issues/new?template=feature_request.yml).
- **Want to submit code or data directly?** Open a pull request. See [CONTRIBUTING.md](CONTRIBUTING.md) for the workflow.

Questions? Start a [Discussion](../../discussions) or email [TangoToolkit@gmail.com](mailto:TangoToolkit@gmail.com).

## Embedding

The viewer can be embedded as an iframe on any site:

```html
<iframe src="https://sericson0.github.io/tango-discographies/"
        width="100%" height="800" frameborder="0"></iframe>
```

To match your site's color theme, pass hex colors (without the `#` prefix) via URL query params:

| Param    | Default  | Controls                                                                  |
| -------- | -------- | ------------------------------------------------------------------------- |
| `bg`     | `f8fafc` | Page background                                                           |
| `text`   | `1e293b` | Primary text color (and basis for muted column text)                      |
| `accent` | `f97316` | Highlights: chips, header underline, hover row, selected row, button borders |

Example with a dark theme:

```html
<iframe src="https://sericson0.github.io/tango-discographies/?bg=0a0a0a&text=eaeaea&accent=fbbf24"
        width="100%" height="800" frameborder="0"></iframe>
```

Invalid or missing params silently fall back to the defaults.

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

### Label images, thumbnails and `singles_manifest.txt`

Record-label images live in a Cloudflare R2 bucket, not in this repo. The
viewer only requests what the bucket really holds:

- **`singles_manifest.txt`** lists every served single key
  (`<Folder>/Singles/<YYYY-YYYY>/<file>.webp`), one per line. A single not in
  the manifest is not shown, so the site never guesses URLs that 404.
- **`thumbs/<key>`** holds a small (240 px max, WEBP) copy of every served
  single and LP/EP image. Table rows load the thumb; the full-size original
  loads only in the detail popup.

Run `sync_thumbs.py` after **any** change to the bucket (`import_singles.py
--upload`, `sync_artist_images.py`, `upload_files.py`, `verify_singles.py
purge --apply`; `finalize_artist.py` runs it for you), then commit
`singles_manifest.txt`:

```bash
python sync_thumbs.py                  # dry run: what would change
python sync_thumbs.py --apply          # write missing/stale thumbs, delete orphans, write manifest
python sync_thumbs.py --manifest-only  # just regenerate singles_manifest.txt
```

It only ever writes or deletes keys under `thumbs/`. The one exception is
`--set-cache-control --apply`, a one-off that rewrites the served originals in
place (same bytes) so they carry the shared `Cache-Control` header.

### Image credits (`image_credits.txt`)

`image_credits.txt` maps served images to the source they came from: every
78 rpm single by its key, every LP/EP/CD cover by its album folder. The viewer
fetches it next to the manifest and shows "Image: <source>" (linked to the
blog post, matrix page, release… when one is known) under the image. Like the
manifest it fails soft: without the file the site works, just without credit
lines.

```bash
python build_image_credits.py           # rebuild image_credits.txt
python build_image_credits.py --check   # report only, write nothing
```

Run it after `sync_thumbs.py --apply` and `build.py`, then commit the file. It
needs the local `images/` tree and the sibling `parse-tango-discographies`
repo, reads both, and never touches R2. It never guesses: a single is credited
only when the served file is the same photograph as a file in one of that
artist's source folders, an album cover only when a fetch log records the
download into that folder. Everything else gets no entry and is covered by the
[Image sources](#image-sources) section alone. The run prints per-source and
per-artist counts, including how many images remain uncredited.

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
