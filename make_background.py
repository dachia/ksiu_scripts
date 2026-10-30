"""Fill a 300x400 mm background with the icons of direktorius.svg, shuffled, each icon whole and at its original size.

Usage: python3 make_background.py [--seed 7] [--out direktorius_bg]
Writes OUT.svg (vector, print file) and OUT.png (preview). The same seed gives the same layout.
Icons are found by grouping source shapes that touch or nearly touch. Big icons fill the page first; small ones then fill the gaps, in the same ratio to big icons as in the source.
Each icon takes the free position farthest from icons of its own colour and size tier, so colours spread evenly.
"""
import argparse, io, math, random, re
import xml.etree.ElementTree as ET
import pymupdf
from PIL import Image, ImageChops, ImageFilter

SOURCE = 'direktorius.svg'
PT_PER_MM = 72 / 25.4
WIDTH_MM, HEIGHT_MM = 300, 400
WIDTH, HEIGHT = WIDTH_MM * PT_PER_MM, HEIGHT_MM * PT_PER_MM
SCALE = 2  # mask pixels per point
JOIN_DISTANCE = 1.2  # pt each shape grows by when grouping; shapes closer than twice this are one icon
EDGE_MARGIN = 6  # pt kept clear along the page edge
TIERS = (  # (smallest icon side in pt, clearance to neighbours in pt), placed in this order
    (24, 7),
    (10, 4),
    (0, 3),
)
MAX_MISSES = 1500  # random positions tried per icon before a tier counts as full
CANDIDATES = 40  # free positions compared per icon; more means more even colours and a less random layout


def read_source():
    root = ET.parse(SOURCE).getroot()
    style = root.find('{http://www.w3.org/2000/svg}defs/{http://www.w3.org/2000/svg}style').text
    fills = dict(re.findall(r'\.([\w-]+)\{fill:(#\w+);\}', style))
    view = root.attrib['viewBox']
    shapes = [(e.tag.split('}')[1], fills[e.attrib['class']], {k: v for k, v in e.attrib.items() if k != 'class'})
              for e in root if e.tag.endswith(('path', 'polygon'))]
    background = fills[root.find('{http://www.w3.org/2000/svg}rect').attrib['class']]
    return view, background, shapes


def shape_svg(shape):
    tag, fill, attrs = shape
    return f'<{tag} fill="{fill}" ' + ' '.join(f'{k}="{v}"' for k, v in attrs.items()) + '/>'


def render_mask(view, body):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}">{body}</svg>'
    pix = pymupdf.open(stream=svg.encode(), filetype='svg')[0].get_pixmap(matrix=pymupdf.Matrix(SCALE, SCALE), alpha=True)
    alpha = Image.frombytes('RGBA', (pix.width, pix.height), pix.samples).getchannel('A')
    return alpha.point(lambda v: 255 if v else 0)


def grow(mask, points):
    radius = round(points * SCALE)
    padded = Image.new('L', (mask.width + 2 * radius, mask.height + 2 * radius))
    padded.paste(mask, (radius, radius))
    # a 3x3 maximum filter grows the mask by one pixel; repeating it keeps the cost low for larger radii
    for _ in range(radius):
        padded = padded.filter(ImageFilter.MaxFilter(3))
    return padded


def group_into_icons(view, shapes):
    """Union shapes whose grown masks overlap. Returns lists of shapes, each list one icon."""
    masks = [render_mask(view, shape_svg(s)) for s in shapes]
    grown = []
    for mask in masks:
        box = mask.getbbox()
        if box is None:
            raise ValueError('source shape renders empty')
        grown.append((box, grow(mask.crop(box), JOIN_DISTANCE)))
    parent = list(range(len(shapes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pad = round(JOIN_DISTANCE * SCALE)
    full_w, full_h = masks[0].size
    for a, (box_a, grown_a) in enumerate(grown):
        for b in range(a + 1, len(grown)):
            box_b, grown_b = grown[b]
            left, top = max(box_a[0], box_b[0]) - pad, max(box_a[1], box_b[1]) - pad
            right, bottom = min(box_a[2], box_b[2]) + pad, min(box_a[3], box_b[3]) + pad
            if left >= right or top >= bottom or find(a) == find(b):
                continue
            canvas_a = Image.new('L', (full_w + 2 * pad, full_h + 2 * pad))
            canvas_a.paste(grown_a, (box_a[0], box_a[1]))
            canvas_b = Image.new('L', canvas_a.size)
            canvas_b.paste(grown_b, (box_b[0], box_b[1]))
            if ImageChops.multiply(canvas_a, canvas_b).getbbox():
                parent[find(a)] = find(b)
    groups = {}
    for i, shape in enumerate(shapes):
        groups.setdefault(find(i), []).append(shape)
    return list(groups.values())


def build_icon(view, shapes):
    full = render_mask(view, ''.join(shape_svg(s) for s in shapes))
    box = full.getbbox()
    mask = full.crop(box)
    areas = {}
    for shape in shapes:
        areas[shape[1]] = areas.get(shape[1], 0) + render_mask(view, shape_svg(shape)).histogram()[255]
    return dict(svg=''.join(shape_svg(s) for s in shapes), mask=mask, origin=(box[0] / SCALE, box[1] / SCALE),
                colour=max(areas, key=areas.get),
                width=mask.width / SCALE, height=mask.height / SCALE)


def fill_tier(icons, clearance, occupied, rng, limit):
    """Deal icons from a reshuffled deck until limit is reached or MAX_MISSES positions in a row fail.
    Each icon gets the free candidate position farthest from placed icons of its colour."""
    pad = round(clearance * SCALE)
    edge = round(EDGE_MARGIN * SCALE)
    stamps = {id(icon): grow(icon['mask'], clearance) for icon in icons}
    placed, deck, centres = [], [], {}
    while len(placed) < limit:
        if not deck:
            deck = rng.sample(icons, len(icons))
        icon = deck.pop()
        stamp = stamps[id(icon)]
        half_w, half_h = icon['mask'].width / 2, icon['mask'].height / 2
        same_colour = centres.setdefault(icon['colour'], [])
        candidates = []
        for _ in range(MAX_MISSES):
            x = rng.randint(edge, occupied.width - edge - icon['mask'].width)
            y = rng.randint(edge, occupied.height - edge - icon['mask'].height)
            box = (x - pad, y - pad, x - pad + stamp.width, y - pad + stamp.height)
            if not ImageChops.multiply(occupied.crop(box), stamp).getbbox():
                candidates.append((x, y))
                if len(candidates) == CANDIDATES:
                    break
        if not candidates:
            break
        x, y = max(candidates, key=lambda c: min((math.dist((c[0] + half_w, c[1] + half_h), p) for p in same_colour), default=0))
        occupied.paste(255, (x, y), icon['mask'])
        same_colour.append((x + half_w, y + half_h))
        placed.append((icon, x / SCALE, y / SCALE))
    return placed


def write_svg(path, background, placed):
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH_MM}mm" height="{HEIGHT_MM}mm" '
             f'viewBox="0 0 {WIDTH:.2f} {HEIGHT:.2f}"><rect width="{WIDTH:.2f}" height="{HEIGHT:.2f}" fill="{background}"/>']
    for icon, x, y in placed:
        dx, dy = x - icon['origin'][0], y - icon['origin'][1]
        parts.append(f'<g transform="translate({dx:.2f} {dy:.2f})">{icon["svg"]}</g>')
    parts.append('</svg>')
    svg = ''.join(parts)
    with open(path, 'w') as f:
        f.write(svg)
    return svg


def write_preview(path, svg):
    pix = pymupdf.open(stream=svg.encode(), filetype='svg')[0].get_pixmap(dpi=150)
    Image.open(io.BytesIO(pix.tobytes('png'))).save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--out', default='direktorius_bg')
    args = parser.parse_args()
    rng = random.Random(args.seed)
    view, background, shapes = read_source()
    icons = [build_icon(view, group) for group in group_into_icons(view, shapes)]
    occupied = Image.new('L', (round(WIDTH * SCALE), round(HEIGHT * SCALE)))
    placed, upper, density = [], float('inf'), None
    for smallest, clearance in TIERS:
        tier = [i for i in icons if smallest <= max(i['width'], i['height']) < upper]
        if not tier:
            raise ValueError(f'no icons with longest side in [{smallest}, {upper}) pt')
        limit = float('inf') if density is None else round(density * len(tier))
        tier_placed = fill_tier(tier, clearance, occupied, rng, limit)
        density = density or len(tier_placed) / len(tier)
        print(f'icons >= {smallest} pt: {len(tier)} distinct, {len(tier_placed)} placed')
        placed += tier_placed
        upper = smallest
    svg = write_svg(f'{args.out}.svg', background, placed)
    write_preview(f'{args.out}.png', svg)
    print(f'{len(shapes)} source shapes, {len(icons)} icons, {len(placed)} placed, wrote {args.out}.svg and {args.out}.png')


if __name__ == '__main__':
    main()
