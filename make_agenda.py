"""Generate one agenda PDF per agenda item, with that single item shown as active.

Usage: python3 make_agenda.py [--base agenda/<Scene1>.pdf] [--out agenda/out]
The base PDF is the agenda with nothing active (Illustrator export). Writes NN_HHMM_<title>.pdf/.png/.jpg per item (in pdf/, png/, jpg/) plus index.csv. --scale sets the PNG/JPEG size.

How an item is found and activated (Illustrator does not keep an item's rectangles and text together in the stream):
  - an item is its two grey rectangles (time cell + text cell): `.945 .945 .945 scn  <r1> re f  <r2> re f`
  - a text run belongs to the item whose rectangles contain the point of its `Tm` operator
  - active = the item's grey fill becomes dark teal in place, its text-showing operators are moved to an overlay
    drawn last in light green (the colours Illustrator wrote in the 'Scene2' PDF, where 09:30 is active)
"""
import argparse, csv, os, re, sys, unicodedata
import pymupdf

BASE = 'agenda/Fusion Georgia 2026 Agenda on Scene1.pdf'
GREY = '.945 .945 .945 scn'                 # spelling of the grey row fill in a cleaned content stream
ACTIVE_FILL = '0 .264 .367 scn'             # #00435E, row background of the active item
ACTIVE_TEXT = '.745 .965 .655 scn'          # #BEF6A7, text of the active item
ITEM = re.compile(r'(?<![\d.])' + re.escape(GREY) + r' (?P<r1>(?:[-\d.]+ ){3}[-\d.]+) re f (?P<r2>(?:[-\d.]+ ){3}[-\d.]+) re f')
JPEG_QUALITY = 95
SHOW_OPS = {'Tj', 'TJ', "'", '"'}
COLOUR_OPS = {'scn', 'sc', 'g', 'rg', 'k', 'cs'}
DELIMS = ' \t\r\n/[]()<>'


def string_end(text, i):
    """Index just past the literal string starting at text[i] == '(' (only backslash escapes and balanced parens count)."""
    depth = 0
    while i < len(text):
        ch = text[i]
        if ch == '\\':
            i += 2
            continue
        depth += (ch == '(') - (ch == ')')
        i += 1
        if depth == 0:
            return i
    sys.exit('unterminated string in the content stream')


def array_end(text, i):
    """Index just past the array starting at text[i] == '['."""
    depth = 0
    while i < len(text):
        ch = text[i]
        if ch == '(':
            i = string_end(text, i)
            continue
        depth += (ch == '[') - (ch == ']')
        i += 1
        if depth == 0:
            return i
    sys.exit('unterminated array in the content stream')


def scan(text):
    """Yield (start, end) of every token of a content stream (strings, arrays, names, numbers, operators)."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == '(':
            j = string_end(text, i)
            yield i, j
            i = j
        elif c == '[':
            j = array_end(text, i)
            yield i, j
            i = j
        elif text.startswith('<<', i) or text.startswith('>>', i):
            yield i, i + 2
            i += 2
        elif c == '<':
            j = text.index('>', i) + 1
            yield i, j
            i = j
        else:
            j = i + 1
            while j < n and text[j] not in DELIMS:
                j += 1
            yield i, j
            i = j


def parse_ops(text):
    """List of dicts: operator, operands, start (first operand), end (operator end), in_text (inside BT..ET)."""
    ops, operands, in_text = [], [], False
    for s, e in scan(text):
        tok = text[s:e]
        if tok[0] in '/([<>' or re.fullmatch(r'[-+]?\d*\.?\d+', tok):
            operands.append((s, tok))
            continue
        start = operands[0][0] if operands else s
        if tok == 'BT':
            in_text = True
        ops.append(dict(op=tok, operands=[t for _, t in operands], start=start, end=e, in_text=in_text))
        if tok == 'ET':
            in_text = False
        operands = []
    return ops


def rect_of(operands):
    x, y, w, h = (float(v) for v in operands.split())  # `re` operator, y-up page space
    return min(x, x + w), min(y, y + h), max(x, x + w), max(y, y + h)


def find_items(stream, page):
    items = []
    for m in ITEM.finditer(stream):
        r1, r2 = rect_of(m['r1']), rect_of(m['r2'])
        box = (min(r1[0], r2[0]), min(r1[1], r2[1]), max(r1[2], r2[2]), max(r1[3], r2[3]))
        top = lambda r: pymupdf.Rect(r[0], page.rect.height - r[3], r[2], page.rect.height - r[1])
        time = page.get_text(clip=top(r1)).strip()
        if not re.fullmatch(r'\d{2}:\d{2}', time):
            sys.exit(f'item at offset {m.start()}: time cell holds {time!r}, expected HH:MM')
        lines = [l.strip() for l in page.get_text(clip=top(r2)).split('\n') if l.strip()]
        items.append(dict(time=time, grey=m.start(), box=box, title=unicodedata.normalize('NFKC', lines[0]) if lines else '', shows=[]))
    if not items:
        sys.exit(f'no agenda items found: expected `{GREY} <rect> re f <rect> re f` in the content stream')
    times = [i['time'] for i in items]
    if len(set(times)) != len(times):
        sys.exit(f'duplicate times in agenda: {sorted(t for t in set(times) if times.count(t) > 1)}')
    return sorted(items, key=lambda i: i['time'])


def assign_text(stream, ops, items):
    """Attach every text-showing operator to the item whose box holds the Tm point of its run.
    Text outside the agenda rows (header, logo) is left alone; text inside the rows' area but in no item is an error."""
    area = (min(i['box'][0] for i in items), min(i['box'][1] for i in items),
            max(i['box'][2] for i in items), max(i['box'][3] for i in items))
    owner, x, y = None, 0, 0
    for o in ops:
        if not o['in_text']:
            continue
        if o['op'] == 'BT':
            owner, x, y = None, None, None
        elif o['op'] == 'Tm':
            x, y = (float(v) for v in o['operands'][4:6])
            hits = [i for i in items if i['box'][0] <= x <= i['box'][2] and i['box'][1] <= y <= i['box'][3]]
            if len(hits) > 1:
                sys.exit(f'text run at ({x}, {y}) lies in several items: {[h["time"] for h in hits]}')
            owner = hits[0] if hits else None
        elif o['op'] in SHOW_OPS and owner is not None:
            owner['shows'].append(o)
        elif o['op'] in SHOW_OPS and x is not None and area[0] <= x <= area[2] and area[1] <= y <= area[3]:
            sys.exit(f'text run at ({x}, {y}) is inside the agenda area but in no item: {stream[o["start"]:o["end"]][:60]!r}')


def activate(stream, ops, item):
    """Dark fill in place, the item's text moved to a light-green overlay appended to the stream."""
    mine = {id(o) for o in item['shows']}
    edits = [(item['grey'], item['grey'] + len(GREY), ACTIVE_FILL)]
    edits += [(o['start'], o['end'], '') for o in item['shows']]
    out = stream
    for s, e, rep in sorted(edits, reverse=True):
        out = out[:s] + rep + out[e:]
    overlay, text_ops = [], []
    for o in ops:
        if not o['in_text']:
            continue
        if o['op'] in COLOUR_OPS or (o['op'] in SHOW_OPS and id(o) not in mine):
            continue
        text_ops.append(stream[o['start']:o['end']])
        if o['op'] == 'ET':
            overlay.append(' '.join(text_ops))
            text_ops = []
    cs = re.search(r'(/\S+) cs', stream)  # the page's RGB colour space; the overlay sits after the stream's closing Q
    if not cs:
        sys.exit('no colour space (cs) operator found in the content stream')
    return out + f'\nq {cs[1]} cs 0 Tc 0 Tw 100 Tz {ACTIVE_TEXT}\n' + '\n'.join(overlay) + '\nQ\n'


def self_check(base_page, out_path, item):
    """The output must have the base's text at the same places; only the active row's text may be (and must be) light."""
    spans = lambda p: sorted((t['text'], tuple(round(v, 2) for v in t['bbox']), t['font'], round(t['size'], 2), t['color'])
                             for b in p.get_text('dict')['blocks'] if b['type'] == 0 for l in b['lines'] for t in l['spans'])
    height = base_page.rect.height
    x0, y0, x1, y1 = item['box']
    row = pymupdf.Rect(x0, height - y1, x1, height - y0)
    before, after = spans(base_page), spans(pymupdf.open(out_path)[0])
    if len(before) != len(after):
        sys.exit(f'{out_path}: {len(after)} text spans, base has {len(before)}')
    light = 0xbef6a7  # ACTIVE_TEXT as pymupdf reports it
    for a, b in zip(before, after):
        if a[:4] != b[:4]:
            sys.exit(f'{out_path}: text {b[0]!r} differs from base {a[0]!r} in position/font/size')
        inside = row.contains(pymupdf.Point((a[1][0] + a[1][2]) / 2, (a[1][1] + a[1][3]) / 2))
        if (inside and b[4] != light) or (not inside and b[4] != a[4]):
            sys.exit(f'{out_path}: {b[0]!r} has colour {b[4]:#08x} (active row: {inside})')


def render(pdf_path, scale, png_path, jpg_path):
    """PNG and JPEG of the PDF's page; scale 1 = the artboard's own pixels (1176 x 1144)."""
    pix = pymupdf.open(pdf_path)[0].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
    for path, kwargs in ((png_path, {}), (jpg_path, {'jpg_quality': JPEG_QUALITY})):
        pix.save(path + '.part', output=os.path.splitext(path)[1][1:].replace('jpg', 'jpeg'), **kwargs)
        os.replace(path + '.part', path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=BASE)
    ap.add_argument('--out', default='agenda/out', help='writes pdf/, png/, jpg/ and index.csv here')
    ap.add_argument('--scale', type=float, default=2, help='PNG/JPEG size relative to the 1176 x 1144 artboard (default 2)')
    args = ap.parse_args()
    folders = {k: os.path.join(args.out, k) for k in ('pdf', 'png', 'jpg')}
    for f in folders.values():
        os.makedirs(f, exist_ok=True)
    base = pymupdf.open(args.base)
    page = base[0]
    page.clean_contents()  # one compact content stream
    stream = page.read_contents().decode('latin-1')
    if len(page.get_contents()) != 1:
        sys.exit('expected one content stream after clean_contents')
    ops = parse_ops(stream)
    items = find_items(stream, page)
    assign_text(stream, ops, items)
    print(f'{len(items)} items found in {args.base}')
    for item in items:
        if not item['shows']:
            sys.exit(f'item {item["time"]} has no text')
    for folder in folders.values():  # outputs of an earlier run and leftovers of an interrupted one
        for stale in (f for f in os.listdir(folder) if f.endswith('.part') or re.fullmatch(r'\d\d_\d{4}_.*\.(pdf|png|jpg)', f)):
            os.remove(os.path.join(folder, stale))
    index = []
    for n, item in enumerate(items, 1):
        doc = pymupdf.open(args.base)
        doc[0].clean_contents()
        doc.update_stream(doc[0].get_contents()[0], activate(stream, ops, item).encode('latin-1'))
        slug = re.sub(r'[^\w]+', '_', item['title']).strip('_')[:40]
        stem = f'{n:02d}_{item["time"].replace(":", "")}_{slug}'
        pdf, png, jpg = (os.path.abspath(os.path.join(folders[k], f'{stem}.{k}')) for k in ('pdf', 'png', 'jpg'))
        doc.save(pdf + '.part', garbage=3, deflate=True)
        self_check(page, pdf + '.part', item)
        os.replace(pdf + '.part', pdf)
        render(pdf, args.scale, png, jpg)
        index.append([n, item['time'], item['title'], pdf, png, jpg])
    with open(os.path.join(args.out, 'index.csv'), 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['№', 'Time', 'Title', 'PDF path', 'PNG path', 'JPEG path'])
        w.writerows(index)
    print(f'wrote {len(index)} PDFs, PNGs and JPEGs + index.csv to {os.path.abspath(args.out)}')


if __name__ == '__main__':
    main()
