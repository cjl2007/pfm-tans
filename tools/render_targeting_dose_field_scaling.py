#!/usr/bin/env python3
"""Render the TANS sequence: pose search, motor reference, and dose selection.

Procedural scientific illustration, not participant anatomy or a FEM solve.
Requires NumPy, Pillow and FFmpeg. Run from any directory:
  python3 tools/render_targeting_dose_field_scaling.py --output /tmp/field-option
The output directory must be new. --preview renders stills and checks only.
"""
import argparse
from bisect import bisect_left
from functools import lru_cache
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS, SECONDS = 1440, 1180, 12, 36
STEM = 'targeting-dose-field-scaling'
PAPER, INK, MUTED = '#f6f5f0', '#202c36', '#586978'
TEAL, CORAL, LIGHT = '#087f83', '#c95b40', '#e8eae5'
THRESHOLD, COLOR_MAX = 70.0, 200.0
MAP_X, MAP_Y, MW, MH = 64, 365, 728, 400
CX, CY, ROTATION = -.16, -.02, -.32
NETWORK_LABELS = [(-.16, -.80, 'Off-target A'), (1.15, -.02, 'Off-target B'),
                  (-.21, .80, 'Off-target C'), (-1.25, .05, 'Off-target D')]
SOURCE = 'https://simnibs.github.io/simnibs/build/html/faq.html#what-is-the-di-dt-in-tms-simulations'


@lru_cache(None)
def font(size, bold=False):
    names = ([Path('/System/Library/Fonts/Supplemental/Arial Bold.ttf'),
              Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')]
             if bold else
             [Path('/System/Library/Fonts/Supplemental/Arial.ttf'),
              Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')])
    for name in names:
        if name.exists():
            return ImageFont.truetype(str(name), size)
    raise RuntimeError('Arial or DejaVu Sans is required.')


def label(draw, xy, text, size=20, color=INK, bold=False, anchor='la'):
    draw.text(xy, text, font=font(size, bold), fill=color, anchor=anchor)


def smooth(x):
    x = max(0., min(1., x))
    return x * x * (3 - 2 * x)


def jet(value):
    """Fixed 0..200 V/m jet scale, never normalized to the frame maximum."""
    t = max(0., min(1., value / COLOR_MAX))
    return tuple(round(255 * max(0., min(1., 1.5 - abs(4 * t - k))))
                 for k in (3, 2, 1))


def xy_to_pixel(x, y):
    return ((x + 1.82) / 3.64 * (MW - 1), (y + 1.0) / 2 * (MH - 1))


def target_radius(theta):
    return .61 * (1 + .14 * math.cos(3 * theta + .6) + .08 * math.sin(2 * theta))


def is_target(x, y):
    u, v = (x - CX) / .94, y - CY
    return math.hypot(u, v) <= target_radius(math.atan2(v, u))


def field_100(x, y):
    """A fixed smooth synthetic field, with 200 V/m peak at 100% MSO."""
    dx, dy = x - CX, y - CY
    u = (dx * math.cos(ROTATION) + dy * math.sin(ROTATION)) / .64
    v = (-dx * math.sin(ROTATION) + dy * math.cos(ROTATION)) / .42
    theta = math.atan2(v, u)
    radius = math.hypot(u, v) / (1 + .11 * math.cos(3 * theta) + .06 * math.sin(2 * theta))
    return 200 * math.exp(-.5 * radius * radius)


def cortical_domain(x, y):
    return abs((x + .035) / 1.74) ** 2.8 + abs(y / .955) ** 2.5 < 1


class Model:
    def __init__(self):
        values, target_values, pixels, domain = [], [], [], []
        for iy in range(MH):
            y = iy / (MH - 1) * 2 - 1
            for ix in range(MW):
                x = ix / (MW - 1) * 3.64 - 1.82
                inside = cortical_domain(x, y)
                value = field_100(x, y)
                pixels.append(round(value / 200 * 255))
                domain.append(255 if inside else 0)
                if inside:
                    values.append(value)
                    if is_target(x, y):
                        target_values.append(value)
        self.field = Image.new('L', (MW, MH))
        self.field.putdata(pixels)
        self.domain = Image.new('L', (MW, MH))
        self.domain.putdata(domain)
        self.values, self.target_values = sorted(values), sorted(target_values)
        self.background = Image.new('RGB', (MW, MH), '#fafbf8')
        self.target_points = []
        for i in range(361):
            theta = i * math.tau / 360
            r = target_radius(theta)
            self.target_points.append(xy_to_pixel(CX + .94 * r * math.cos(theta), CY + r * math.sin(theta)))
        self.boundaries = []
        for theta in (-2.25, -.8, .48, 2.15):
            points = []
            for i in range(161):
                r = target_radius(theta) + i / 160 * 1.85
                a = theta + .12 * math.sin(i / 160 * 4)
                x, y = CX + .94 * r * math.cos(a), CY + r * math.sin(a)
                if not cortical_domain(x, y):
                    break
                points.append(xy_to_pixel(x, y))
            self.boundaries.append(points)

    def pose_field(self, pose):
        """Move a synthetic field coherently with the coil; no physical coil solve."""
        dx, dy, angle = pose
        y, x = np.mgrid[-1:1:complex(MH), -1.82:1.82:complex(MW)]
        x, y = x - CX - dx, y - CY - dy
        rotation = ROTATION + angle
        u = (x * np.cos(rotation) + y * np.sin(rotation)) / .64
        v = (-x * np.sin(rotation) + y * np.cos(rotation)) / .42
        theta = np.arctan2(v, u)
        radius = np.hypot(u, v) / (1 + .11*np.cos(3*theta) + .06*np.sin(2*theta))
        return Image.fromarray(np.rint(255*np.exp(-.5*radius**2)).astype('uint8'))

    @lru_cache(None)
    def selected_dose(self):
        # Illustrative binary objective, matching the selector's constraint logic.
        minimum = .5 * len(self.target_values)
        feasible = [self.stats(i/10) for i in range(351,801)
                    if self.stats(i/10)['hotspot_pixels'] >= minimum]
        return min(feasible, key=lambda r: (-r['specificity'], r['mso']))['mso']

    def stats(self, mso):
        cutoff = THRESHOLD * 100 / mso
        count = len(self.values) - bisect_left(self.values, cutoff)
        on_target = len(self.target_values) - bisect_left(self.target_values, cutoff)
        return {'mso': mso, 'peak_vm': 2 * mso,
                'coverage': on_target / len(self.target_values),
                'specificity': on_target / count if count else None,
                'hotspot_pixels': count, 'target_hotspot_pixels': on_target,
                'off_target_pixels': count - on_target}

    def threshold_contour(self, mso):
        if mso * 2 <= THRESHOLD:
            return []
        radius = math.sqrt(2 * math.log(mso * 2 / THRESHOLD))
        points = []
        for i in range(361):
            theta = i * math.tau / 360
            r = radius * (1 + .11 * math.cos(3 * theta) + .06 * math.sin(2 * theta))
            u, v = .64 * r * math.cos(theta), .42 * r * math.sin(theta)
            x = CX + u * math.cos(ROTATION) - v * math.sin(ROTATION)
            y = CY + u * math.sin(ROTATION) + v * math.cos(ROTATION)
            points.append(xy_to_pixel(x, y))
        return points

    def map_image(self, mso, reveal, annotate=True, pose=(0., 0., 0.)):
        # A fixed field raster is recolored with a fixed absolute scale.
        # Only the below-threshold display is faded; the field never deforms.
        colors = []
        for i in range(256):
            value = i / 255 * 200 * mso / 100
            rgb = jet(value)
            if value < THRESHOLD:
                rgb = tuple(round(a * (1 - reveal) + b * reveal)
                            for a, b in zip(rgb, (231, 236, 233)))
            colors.append(rgb)
        raster = self.field if pose == (0., 0., 0.) else self.pose_field(pose)
        colored = Image.merge('RGB', tuple(raster.point([c[k] for c in colors]) for k in range(3)))
        result = Image.composite(colored, self.background, self.domain)
        draw = ImageDraw.Draw(result)
        for points in self.boundaries:
            draw.line(points, fill='#f7f7ed', width=4, joint='curve')
            draw.line(points, fill='#4a575e', width=2, joint='curve')
        draw.line(self.target_points, fill='white', width=8, joint='curve')
        draw.line(self.target_points, fill=INK, width=4, joint='curve')
        if reveal > .1:
            points = self.threshold_contour(mso)
            for start in range(0, len(points) - 1, 10):
                segment = points[start:start + 7]
                draw.line(segment, fill='#28363e', width=5, joint='curve')
                draw.line(segment, fill='white', width=3, joint='curve')
        if not annotate:
            return result
        for x, y, name in NETWORK_LABELS:
            px, py = xy_to_pixel(x, y)
            box = draw.textbbox((px, py), name, font=font(16, True), anchor='mm')
            draw.rounded_rectangle((box[0] - 8, box[1] - 5, box[2] + 8, box[3] + 5), radius=6, fill='#f6f6ee')
            label(draw, (px, py), name, 16, bold=True, anchor='mm')
        tx, ty = xy_to_pixel(CX, CY + .23)
        draw.rounded_rectangle((tx - 76, ty - 15, tx + 76, ty + 15), radius=6, fill=INK)
        label(draw, (tx, ty), 'Target network', 16, 'white', True, 'mm')
        return result


def search_pose(t):
    knots = [(0., (.95, -.12, .60)), (1., (.95, -.12, .60)),
             (2.8, (-.65, .10, -.55)), (4., (0., 0., -.85)),
             (6., (0., 0., .85)), (7.3, (0., 0., 0.)), (9., (0., 0., 0.))]
    for (ta,a),(tb,b) in zip(knots,knots[1:]):
        if t <= tb:
            q=smooth((t-ta)/(tb-ta))
            return tuple(v+(w-v)*q for v,w in zip(a,b))
    return (0.,0.,0.)


def timeline(t, selected):
    if t < 9: return 0, 60., 0., search_pose(t)
    if t < 15: return 1, 60., smooth((t-12)/1.5), (0.,0.,0.)
    if t < 19: mso=36.
    elif t < 23: mso=36+(selected-36)*smooth((t-19)/2)
    elif t < 28: mso=selected+(80-selected)*smooth((t-23)/2)
    elif t < 31: mso=80+(selected-80)*smooth((t-28)/2)
    else: mso=selected
    return 2, mso, 1., (0.,0.,0.)


def coil_outline(draw, pose, selected):
    dx,dy,angle=pose
    px,_=xy_to_pixel(CX+dx,CY+dy)
    cx,cy=MAP_X+px,322+dy*40
    def pt(x,y):
        x, y = .82*x, .82*y
        return (cx+x*math.cos(angle)-y*math.sin(angle),cy+x*math.sin(angle)+y*math.cos(angle))
    color=TEAL if selected else INK
    for center in (-29,29):
        points=[pt(center+29*math.cos(i*math.tau/80),18*math.sin(i*math.tau/80)) for i in range(81)]
        draw.line(points,fill=color,width=4,joint='curve')
    draw.line([pt(0,-4),pt(0,-43)],fill=color,width=5)
    draw.ellipse((cx-3,cy-3,cx+3,cy+3),fill=color)
    label(draw,(872,267),'Pose selected' if selected else f'Orientation {math.degrees(angle):+.0f}°',16,color,True,'ra')


def metric_bar(draw, y, title, value, description, color):
    label(draw, (948, y), title, 21, bold=True)
    label(draw, (1370, y - 2), '—' if value is None else f'{value:.0%}', 25, color, True, 'ra')
    draw.rounded_rectangle((948, y + 34, 1370, y + 45), radius=5, fill=LIGHT)
    if value:
        draw.rounded_rectangle((948, y + 34, 948 + 422 * value, y + 45), radius=5, fill=color)
    label(draw, (948, y + 55), description, 16, MUTED)


def frame(model, t):
    selected=model.selected_dose()
    phase,mso,reveal,pose=timeline(t,selected)
    stats=model.stats(mso)
    im=Image.new('RGB',(W,H),PAPER);draw=ImageDraw.Draw(im)
    label(draw,(40,27),'Target the network. Then choose the dose.',37,bold=True)
    label(draw,(40,80),'TANS links coil placement, a motor-derived field reference, and individualized dose selection.',22,MUTED)
    for i,title in enumerate(['Search coil poses','Estimate the reference','Balance dose and exposure']):
        x=40+i*461
        draw.rounded_rectangle((x,130,x+438,187),radius=13,fill=(CORAL if i==2 else TEAL) if i==phase else LIGHT)
        label(draw,(x+18,148),f'{i+1}  {title}',20,'white' if i==phase else MUTED,True)
    draw.rounded_rectangle((40,210,896,829),radius=20,fill='white')
    draw.rounded_rectangle((920,210,1400,829),radius=20,fill='white')
    label(draw,(64,232),'AN INDIVIDUAL’S NETWORKS + MODELED FIELD',17,MUTED,True)
    label(draw,(64,253),'Coil outline above cortex (schematic)',16,MUTED)
    coil_outline(draw,pose,t>=7.3)
    im.paste(model.map_image(mso,reveal,pose=pose),(MAP_X,MAP_Y))
    label(draw,(819,360),'|E|',20,bold=True);label(draw,(812,386),'V/m',16,MUTED)
    for y in range(294): draw.line((821,419+y,841,419+y),fill=jet(200*(1-y/293)))
    for value in (0,50,100,150,200):
        y=419+(1-value/200)*293
        label(draw,(852,y),str(value),14,MUTED,anchor='lm')
    if reveal>.1:
        y=419+(1-THRESHOLD/200)*293
        draw.polygon([(814,y-6),(821,y),(814,y+6)],fill=INK)
        label(draw,(807,y),'70',15,INK,True,'rm')
    label(draw,(811,734),'Fixed scale',12,MUTED)
    draw.line((66,797,98,797),fill=INK,width=4)
    label(draw,(108,786),'Target boundary',16,MUTED)
    if reveal>.1:
        for x in range(307,340,12): draw.line((x,797,x+8,797),fill=INK,width=3)
        label(draw,(349,786),'70 V/m contour',16,MUTED)
        draw.rectangle((550,788,565,802),fill='#e7ece9',outline='#cbd3cf')
        label(draw,(578,786),'Below reference',16,MUTED)
    else: label(draw,(330,786),'Full field shown • no absolute threshold applied',16,MUTED)
    if phase==0:
        label(draw,(948,235),'POSITION + ORIENTATION',20,bold=True)
        for y,text in [(290,'Move the field relative to the'),(320,'target network and its neighbors.'),
                       (398,'Search for a pose that concentrates'),(428,'the strongest field on target.'),
                       (510,'TANS scores relative-field hotspots'),(540,'across field percentiles.'),
                       (622,'A motor-derived threshold is'),(652,'not needed for this search.')]:
            label(draw,(948,y),text,20 if y<500 else 18,MUTED)
        label(draw,(948,739),'Selected pose → hold it fixed',23,TEAL,True)
    elif phase==1:
        label(draw,(948,235),'MOTOR-REFERENCE LOGIC',20,bold=True)
        for y,text in [(288,'Observed resting motor threshold'),(318,'(rMT) at the motor-reference pose'),
                       (369,'+ simulated motor E-field'),(410,'+ matched hardware calibration'),
                       (466,'↓'),(510,'Estimated field reference')]:
            label(draw,(948,y),text,19,MUTED)
        label(draw,(948,551),'70 V/m',39,TEAL,True)
        label(draw,(948,604),'Illustrative value for this diagram',17,MUTED)
        label(draw,(948,659),'Apply this reference at the fixed',19,bold=True)
        label(draw,(948,689),'treatment pose, then vary dose.',19,bold=True)
        label(draw,(948,761),'See the linked rMT / E-field procedures.',17,TEAL)
    else:
        label(draw,(948,235),'DOSE AT THE FIXED POSE',18,MUTED,True)
        label(draw,(948,267),f'{mso:.1f}',60,bold=True)
        label(draw,(1100,309),'% MSO',23,MUTED,True)
        label(draw,(1370,287),f'{mso*2:.0f} V/m',23,TEAL,True,'ra')
        label(draw,(1370,324),'peak field',16,MUTED,anchor='ra')
        draw.rounded_rectangle((951,374,1367,383),radius=4,fill=LIGHT)
        sx=951+(mso-20)/60*416
        draw.rounded_rectangle((951,374,sx,383),radius=4,fill=TEAL)
        draw.ellipse((sx-9,369,sx+9,387),fill=TEAL)
        for v in (20,40,60,80): label(draw,(951+(v-20)/60*416,397),str(v),15,MUTED,anchor='ma')
        draw.rounded_rectangle((944,440,1375,497),radius=10,fill='#f0f2ec')
        label(draw,(958,451),'FIXED FIELD REFERENCE',14,MUTED,True)
        label(draw,(1358,447),'70 V/m',29,INK,True,'ra')
        label(draw,(958,474),'Hotspot = cortex above this reference',15,MUTED)
        metric_bar(draw,522,'Target coverage',stats['coverage'],'Fraction of target above the reference',TEAL)
        metric_bar(draw,625,'Target specificity',stats['specificity'],'Fraction of hotspot in the target network',CORAL)
        enough=stats['hotspot_pixels']>=.5*len(model.target_values)
        label(draw,(948,733),'Minimum exposure: '+('met' if enough else 'not met'),20,TEAL if enough else CORAL,True)
        label(draw,(948,768),'Illustrative area constraint; see caption.',16,MUTED)
    draw.rounded_rectangle((40,850,1400,1054),radius=20,fill='white')
    if phase==0:
        label(draw,(64,874),'SEARCH CHANGES THE FIELD’S PLACEMENT',22,bold=True)
        label(draw,(64,921),'Coil position and orientation change where the field falls relative to the network boundaries.',21,MUTED)
        label(draw,(64,972),'Once a pose is selected, the dose sweep holds that spatial pattern fixed.',24,TEAL,True)
    else:
        label(draw,(64,866),'FIXED POSE: SAME RELATIVE FIELD AT EVERY DOSE',19,bold=True)
        normalized=model.map_image(100,0,annotate=False).resize((180,99),Image.Resampling.LANCZOS)
        for x,ms in [(64,30),(276,60),(488,80)]:
            label(draw,(x+90,895),f'{ms}% MSO',16,bold=True,anchor='ma')
            im.paste(normalized,(x,920))
            label(draw,(x+90,1024),f'Peak {ms*2} V/m',15,TEAL,True,'ma')
        label(draw,(723,890),'Same relative pattern; increasing absolute values.',22,bold=True)
        label(draw,(723,934),'Each map uses |E| / peak on the same 0–1 scale.',18,MUTED)
        label(draw,(723,976),'The area above the fixed reference can expand.',20,TEAL,True)
    if phase==0: headline='First place the strongest field within the target network.'
    elif phase==1: headline='Use the motor measurement to estimate a field reference for dosing.'
    elif t<19: headline='High specificity can still mean very little target coverage.'
    elif t<23: headline='Increasing dose exposes more of the target network.'
    elif t<28: headline='Higher dose also exposes off-target networks: coverage has a cost.'
    else: headline='TANS favors selectivity while meeting the configured exposure and output limits.'
    label(draw,(40,1072),headline,24,bold=True)
    label(draw,(40,1120),'Synthetic illustration, not a FEM simulation. The 70 V/m reference and linear MSO scaling are illustrative.',16,MUTED)
    label(draw,(40,1149),'Above-reference exposure is modeled stimulation, not proof of neuronal activation. Coil outline is not to scale.',16,MUTED)
    return im


def network_at(x, y):
    if not cortical_domain(x, y):
        return None
    if is_target(x, y):
        return 'Target'
    radius = math.hypot((x - CX) / .94, y - CY)
    angle = math.atan2(y - CY, (x - CX) / .94)
    edges = []
    for theta in (-2.25, -.8, .48, 2.15):
        t = max(0., min(1., (radius - target_radius(theta)) / 1.85))
        edges.append(theta + .12 * math.sin(t * 4))
    for i, name in enumerate(('Off-target A', 'Off-target B', 'Off-target C')):
        if edges[i] <= angle < edges[i + 1]:
            return name
    return 'Off-target D'


def validate(model):
    for x, y, name in NETWORK_LABELS:
        assert network_at(x, y) == name, (name, network_at(x, y))
        # Check the entire label box stays inside the named region.
        box = font(16, True).getbbox(name)
        dx = ((box[2] - box[0]) / 2 + 8) / (MW - 1) * 3.64
        dy = ((box[3] - box[1]) / 2 + 5) / (MH - 1) * 2
        for xx in (x - dx, x + dx):
            for yy in (y - dy, y + dy):
                assert network_at(xx, yy) == name, (name, xx, yy, network_at(xx, yy))
    assert network_at(CX - .07, CY + .35) == 'Target'
    # Scientific invariants, not rendering snapshots.
    for x, y in [(CX, CY), (.65, .12), (-.5, .4)]:
        e30, e60 = field_100(x, y) * .30, field_100(x, y) * .60
        assert math.isclose(e60, 2 * e30)
    for mso in (40, 50, 60, 80):
        for px, py in model.threshold_contour(mso)[::30]:
            x, y = px / (MW - 1) * 3.64 - 1.82, py / (MH - 1) * 2 - 1
            assert math.isclose(field_100(x, y) * mso / 100, THRESHOLD, rel_tol=1e-8)
    rows = [model.stats(mso) for mso in (30, 36, 50, 60, 80)]
    assert rows[0]['hotspot_pixels'] == 0 and rows[0]['specificity'] is None
    assert all(a['hotspot_pixels'] <= b['hotspot_pixels'] for a, b in zip(rows, rows[1:]))
    assert rows[-1]['coverage'] > rows[1]['coverage']
    assert rows[-1]['specificity'] < rows[1]['specificity']
    assert rows[-1]['off_target_pixels'] > rows[1]['off_target_pixels']
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--preview', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    model = Model()
    rows = validate(model)
    checks = {'conceptual_only': True, 'participant_data': False, 'simulation_data': False,
              'source': SOURCE, 'threshold_vm': THRESHOLD, 'color_scale_vm': [0, COLOR_MAX],
              'selected_illustrative_mso': model.selected_dose(),
              'minimum_hotspot_area': 'Illustrative: half the target area in equal-area schematic pixels; not a shipped setting.',
              'selection': 'Maximum binary specificity subject to minimum hotspot area, positive target overlap and 80% illustrative output ceiling; lowest-dose tie break.',
              'pose_search': 'Synthetic translation plus a fixed-position orientation sweep before locking the pose; not FEM solutions or actual optimizer scores.',
              'duration_seconds': SECONDS, 'frames_per_second': FPS,
              'assumption': 'E scales exactly with dI/dt; idealized linear dI/dt-to-MSO calibration here.',
              'area_measure': 'Equal-area schematic raster samples, not anatomical mm2.',
              'validation': 'Linear scaling, fixed spatial field, absolute colors, contour values, nested hotspot areas and network label box ownership checked.',
              'normalized_insets': 'Three byte-identical normalized rasters after pose selection; absolute peak values differ.',
              'network_label_regions': {name: [x,y] for x,y,name in NETWORK_LABELS},
              'illustrative_metrics': rows}
    (args.output / f'{STEM}.json').write_text(json.dumps(checks, indent=2) + '\n')
    frame(model, 33).save(args.output / f'{STEM}.png')
    # Four full-resolution review frames arranged at half scale.
    storyboard = Image.new('RGB', (W, H), PAPER)
    for i, t in enumerate((2, 12, 17, 33)):
        still = frame(model, t)
        still.save(args.output / f'{STEM}-step-{i + 1}.png')
        storyboard.paste(still.resize((W // 2, H // 2), Image.Resampling.LANCZOS), ((i % 2) * W // 2, (i // 2) * H // 2))
    storyboard.save(args.output / f'{STEM}-storyboard.png')
    if args.preview:
        print(json.dumps(checks, indent=2))
        return
    if not shutil.which('ffmpeg'):
        raise RuntimeError('FFmpeg is required for animation output.')
    mp4 = args.output / f'{STEM}.mp4'
    with tempfile.TemporaryFile() as errors:
        command = ['ffmpeg', '-v', 'error', '-n', '-f', 'rawvideo', '-pixel_format', 'rgb24',
                   '-video_size', f'{W}x{H}', '-framerate', str(FPS), '-i', '-', '-an',
                   '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(mp4)]
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors)
        try:
            for i in range(FPS * SECONDS):
                proc.stdin.write(frame(model, i / FPS).tobytes())
                if i % (FPS * 4) == 0:
                    print(f'Rendered {i / FPS:.0f}/{SECONDS}s', flush=True)
            proc.stdin.close()
            code = proc.wait()
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        if code:
            errors.seek(0)
            raise RuntimeError(errors.read().decode())
    subprocess.run(['ffmpeg', '-v', 'error', '-n', '-i', str(mp4), '-filter_complex',
                    '[0:v]split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3',
                    '-loop', '0', str(args.output / f'{STEM}.gif')], check=True)
    print(args.output / f'{STEM}.gif', flush=True)


if __name__ == '__main__':
    main()
