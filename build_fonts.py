"""Build static Roboto Regular/Bold from a variable Roboto, with names matching the ones in pvz.ai
(Illustrator matches fonts by name: Roboto-Bold / Roboto-Regular)."""
import os
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

SOURCE = '/home/dachia/Projects/agent/agent-be/backend/modules/pptx_restyle/fonts/roboto.ttf'
STYLES = {'Regular': 400, 'Bold': 700}

os.makedirs('fonts', exist_ok=True)
for style, weight in STYLES.items():
    font = TTFont(SOURCE)
    axes = {a.axisTag: (weight if a.axisTag == 'wght' else a.defaultValue) for a in font['fvar'].axes}
    instancer.instantiateVariableFont(font, axes, inplace=True)
    for rec in list(font['name'].names):
        if rec.nameID in (1, 2, 4, 6, 16, 17, 25):
            font['name'].removeNames(nameID=rec.nameID)
    for nid, text in ((1, 'Roboto'), (2, style), (4, f'Roboto {style}'), (6, f'Roboto-{style}')):
        font['name'].setName(text, nid, 3, 1, 0x409)
        font['name'].setName(text, nid, 1, 0, 0)
    bold = style == 'Bold'
    os2 = font['OS/2']
    os2.usWeightClass = weight
    os2.fsSelection = (os2.fsSelection & ~0x61) | (0x20 if bold else 0x40)  # clear italic/bold/regular, set one
    font['head'].macStyle = 1 if bold else 0
    font.save(f'fonts/Roboto-{style}.ttf')
