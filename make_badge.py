"""Generate one badge PDF per participant from pvz.ai, driven only by badges.csv.

Usage: python3 make_badge.py [--set DIR] [--rows 8,20,160]    (no --rows = every row)
--set DIR uses DIR/badges.csv and writes DIR/pdf and DIR/tiff instead of the default badges.csv, badges_pdf, badges_tiff.
Reads badges.csv, writes badges_pdf/NNN_Name.pdf and badges_tiff/NNN_Name.tif (CMYK, 300 dpi, LZW), and records
Template colour / PDF path / TIFF path / Badge status back into the same badges.csv. Edit QR path or Category in badges.csv to correct a badge, then rerun.
Template pages: 0 = green (Banking), 1 = orange (Retail), 2 = blue (anything else).
"""
import argparse, csv, glob, os, re, sys
import pymupdf

TEMPLATE, SLIM = 'pvz.ai', 'template_slim.pdf'
BADGES_CSV, OUT_DIR, TIFF_DIR, BLANK_QR = 'badges.csv', 'badges_pdf', 'badges_tiff', 'blank_qr.png'
CMYK_PROFILE = 'profiles/USWebCoatedSWOPv2.icc'  # the CMYK profile of the photo in pvz.ai
WILL_NOT_ATTEND = 'не будет'
COLOURS = ('green', 'orange', 'blue')
UNSAFE_FILENAME_CHARS = re.compile(r'[^\w-]+')
BOLD, REGULAR = pymupdf.Font(fontfile='fonts/Roboto-Bold.ttf'), pymupdf.Font(fontfile='fonts/Roboto-Regular.ttf')
TEXT_COLOR = (0x06 / 255, 0x43 / 255, 0x5E / 255)
MAX_TEXT_WIDTH = 246  # page is 286.3 pt wide; 20 pt margin each side
NAME = dict(old_bbox=(50, 244, 236, 280), baseline=271.5, size=24, min_size=12, font=BOLD)
COMPANY = dict(old_bbox=(50, 277, 236, 297), baseline=291.8, size=12, min_size=7, font=REGULAR)


def slim_template():
    # pvz.ai is 370 MB: Illustrator private data plus a 6000x4005 CMYK photo (1111 dpi) shared by all pages.
    # Downscale the photo to 1800 px (~333 dpi) and copy the pages into a fresh PDF, which drops the private data.
    if os.path.exists(SLIM):
        return
    import io
    from PIL import Image
    src = pymupdf.open(TEMPLATE)
    xref = next(i[0] for i in src[0].get_images() if i[2] == 6000)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.Pixmap(src, xref))
    img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples).resize((1800, 1201), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=90)
    src[0].replace_image(xref, stream=buf.getvalue())
    slim = pymupdf.open()
    slim.insert_pdf(src)
    slim.save(SLIM, garbage=3, deflate=True)


def page_index(category):
    return {'banking': 0, 'retail': 1}.get(category.strip().lower(), 2)


def put_centered(page, text, spec):
    size = spec['size']
    while spec['font'].text_length(text, size) > MAX_TEXT_WIDTH and size > spec['min_size']:
        size -= 0.25
    width = spec['font'].text_length(text, size)
    if width > MAX_TEXT_WIDTH:
        raise ValueError(f'text does not fit at min size {spec["min_size"]}: {text!r}')
    tw = pymupdf.TextWriter(page.rect, color=TEXT_COLOR)
    tw.append(((page.rect.width - width) / 2, spec['baseline']), text, font=spec['font'], fontsize=size)
    tw.write_text(page)
    return size


def use_postscript_font_names(doc, page):
    # PyMuPDF names the Type0 font and its descriptor 'Roboto Bold' (family + style) but the CID font 'Roboto-Bold'.
    # Illustrator matches fonts by name, so make all three the PostScript name the template uses.
    for xref, *_, name, _ref, _enc, _ in page.get_fonts(full=True):
        if name not in ('Roboto Bold', 'Roboto Regular'):
            continue
        ps = '/' + name.replace(' ', '-')
        doc.xref_set_key(xref, 'BaseFont', ps)
        desc = doc.xref_get_key(xref, 'DescendantFonts')[1].strip('[] ').split()[0]
        fd = doc.xref_get_key(int(desc), 'FontDescriptor')[1].split()[0]
        doc.xref_set_key(int(fd), 'FontName', ps)


def cmyk_profile():
    if not os.path.exists(CMYK_PROFILE):
        src = pymupdf.open(TEMPLATE)
        photo = next(i[0] for i in src[0].get_images() if i[2] == 6000)
        cs = int(re.search(r'(\d+) 0 R', src.xref_get_key(photo, 'ColorSpace')[1]).group(1))
        icc = int(re.search(r'ICCBased (\d+) 0 R', src.xref_object(cs, compressed=True)).group(1))
        os.makedirs(os.path.dirname(CMYK_PROFILE), exist_ok=True)
        with open(CMYK_PROFILE, 'wb') as fh:
            fh.write(src.xref_stream(icc))
    return CMYK_PROFILE


def export_tiff(pdf_path, tiff_path):
    from PIL import Image, ImageCms
    pix = pymupdf.open(pdf_path)[0].get_pixmap(dpi=300, alpha=False)
    rgb = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    cmyk_profile_file = cmyk_profile()
    cmyk = ImageCms.profileToProfile(rgb, ImageCms.createProfile('sRGB'), cmyk_profile_file, outputMode='CMYK',
                                     renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
    with open(cmyk_profile_file, 'rb') as fh:
        cmyk.save(tiff_path + '.part', format='TIFF', compression='tiff_lzw', dpi=(300, 300), icc_profile=fh.read())
    os.replace(tiff_path + '.part', tiff_path)  # an interrupted run never leaves a truncated .tif


def make_badge(row, out_path):
    if not row['Name'].strip():
        raise ValueError('empty Name')
    qr_path = row['QR path'] or BLANK_QR
    if not os.path.exists(qr_path):
        raise FileNotFoundError(f'QR path does not exist: {qr_path}')
    doc = pymupdf.open(SLIM)
    doc.select([page_index(row['Category'])])
    page = doc[0]
    for spec in (NAME, COMPANY):
        page.add_redact_annot(pymupdf.Rect(spec['old_bbox']))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    put_centered(page, row['Name'].strip(), NAME)
    if row['Company'].strip():
        put_centered(page, row['Company'].strip(), COMPANY)
    qr_xref = next((i[0] for i in page.get_images() if i[2] == 738 and i[3] == 738), None)
    if qr_xref is None:
        raise ValueError('template page has no 738x738 QR image')
    qr_rect = next(i['bbox'] for i in page.get_image_info(xrefs=True) if i['xref'] == qr_xref)
    page.delete_image(qr_xref)
    page.insert_image(qr_rect, filename=qr_path)
    use_postscript_font_names(doc, page)
    doc.save(out_path + '.part', garbage=4, deflate=True)
    os.replace(out_path + '.part', out_path)  # an interrupted run never leaves a truncated .pdf


def remove_old_outputs(row, keep=()):
    # badges.csv is hand-editable: compare normalised paths and only ever delete inside our own output folders
    keep = {os.path.abspath(k) for k in keep}
    for old, folder in ((row['PDF path'], OUT_DIR), (row['TIFF path'], TIFF_DIR)):
        old = os.path.abspath(old) if old else ''
        if old and old not in keep and os.path.dirname(old) == os.path.abspath(folder) and os.path.exists(old):
            os.remove(old)


def process_row(row):
    """Build (or skip) one badge and fill the output columns of its CSV row."""
    if WILL_NOT_ATTEND in row['Attendance note'].lower():
        remove_old_outputs(row)
        row.update({'Template colour': '', 'PDF path': '', 'TIFF path': '', 'Badge status': 'skipped - will not attend'})
        return
    stem = UNSAFE_FILENAME_CHARS.sub('_', row['Name'].strip())
    out_path = os.path.abspath(os.path.join(OUT_DIR, f'{int(row["№"]):03d}_{stem}.pdf'))
    tiff_path = os.path.abspath(os.path.join(TIFF_DIR, f'{int(row["№"]):03d}_{stem}.tif'))
    make_badge(row, out_path)
    export_tiff(out_path, tiff_path)
    remove_old_outputs(row, keep=(out_path, tiff_path))  # name changed since the last run
    row.update({'Template colour': COLOURS[page_index(row['Category'])], 'PDF path': out_path, 'TIFF path': tiff_path,
                'Badge status': 'ok' if row['QR path'] else 'ok - no QR'})


def main():
    global BADGES_CSV, OUT_DIR, TIFF_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument('--set', help='badge set directory holding badges.csv; outputs go to its pdf/ and tiff/')
    ap.add_argument('--rows', help='comma-separated № values to process; default all')
    args = ap.parse_args()
    if args.set:
        BADGES_CSV, OUT_DIR, TIFF_DIR = (os.path.join(args.set, p) for p in ('badges.csv', 'pdf', 'tiff'))
    wanted = set(args.rows.split(',')) if args.rows is not None else None
    with open(BADGES_CSV, encoding='utf-8-sig', newline='') as fh:
        reader = csv.DictReader(fh)
        fields, rows = reader.fieldnames, list(reader)
    if wanted is not None and not wanted <= {r['№'] for r in rows}:
        sys.exit(f'unknown row numbers: {sorted(wanted - {r["№"] for r in rows})}')
    slim_template()
    if not os.path.exists(BLANK_QR):
        from PIL import Image
        Image.new('RGB', (738, 738), 'white').save(BLANK_QR)
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(TIFF_DIR, exist_ok=True)
    for folder in (OUT_DIR, TIFF_DIR):  # leftovers of an interrupted run
        for leftover in glob.glob(os.path.join(folder, '*.part')):
            os.remove(leftover)
    failed = []
    try:
        for row in rows:
            if wanted is not None and row['№'] not in wanted:
                continue
            try:
                process_row(row)
            except Exception as e:
                row['Badge status'] = f'error: {e!r}'
                failed.append(row['№'])
                print(f'row {row["№"]} {row["Name"]}: {e!r}', file=sys.stderr)
    finally:  # keep the status of finished rows even if the run is interrupted
        with open(BADGES_CSV + '.tmp', 'w', encoding='utf-8-sig', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        os.replace(BADGES_CSV + '.tmp', BADGES_CSV)
    print(f'processed {sum(1 for r in rows if wanted is None or r["№"] in wanted)} rows, {len(failed)} errors {failed}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
