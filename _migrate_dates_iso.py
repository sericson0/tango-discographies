import csv, glob, re

US = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

def to_iso(v):
    v = (v or "").strip()
    m = US.match(v)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return v  # ISO / year-only / year-month / empty / other: untouched

def main():
    changed_files = 0
    changed_cells = 0
    report = []
    for fp in sorted(glob.glob("csv_files/*.csv")):
        with open(fp, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        if not rows:
            continue
        header = rows[0]
        if "Date" not in header:
            continue
        di = header.index("Date")
        fchanged = 0
        for r in rows[1:]:
            if di < len(r):
                new = to_iso(r[di])
                if new != r[di]:
                    r[di] = new
                    fchanged += 1
        if fchanged:
            with open(fp, "w", encoding="utf-8-sig", newline="") as f:
                csv.writer(f, lineterminator="\r\n").writerows(rows)
            changed_files += 1
            changed_cells += fchanged
            report.append((fp, fchanged))
    print(f"files changed: {changed_files}, cells changed: {changed_cells}")
    for fp, n in report:
        print(f"  {fp}: {n}")

if __name__ == "__main__":
    main()
