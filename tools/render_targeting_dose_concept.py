#!/usr/bin/env python3
"""Render a code-native shape-sorter analogy; no participant or simulation data.

Requires system Python with PyGObject/Rsvg, pycairo, and FFmpeg. Writes a GIF,
PNG poster, and editable SVG poster to a NEW directory, refusing overwrite.
Example: /usr/bin/python3 tools/render_targeting_dose_concept.py --output /tmp/concept
"""
import argparse
from html import escape
import math
from pathlib import Path
import subprocess
import tempfile

import cairo
import gi
gi.require_version('Rsvg', '2.0')
from gi.repository import Rsvg

WIDTH, HEIGHT, FPS, DURATION = 1440, 840, 12, 14
INK, MUTED, TEAL, ORANGE, PAPER = '#202a35', '#596777', '#149b9b', '#dd7053', '#f6f5f0'


def smooth(value):
    value = min(1., max(0., value))
    return value * value * (3 - 2 * value)


def shape(scale=1., angle=0., offset=(0., 0.), count=72):
    # An asymmetric rounded three-lobed toy shape, not an anatomical network.
    points = []
    for index in range(count):
        theta = index * math.tau / count
        radius = 104 * (1 + .19 * math.cos(3 * theta + .35) + .12 * math.sin(2 * theta))
        x, y = scale * radius * math.cos(theta), scale * radius * math.sin(theta)
        points.append((x * math.cos(angle) - y * math.sin(angle) + offset[0],
                       x * math.sin(angle) + y * math.cos(angle) + offset[1]))
    return points


def path(points):
    return 'M' + ' L'.join(f'{x:.2f},{y:.2f}' for x, y in points) + ' Z'


def text(x, y, value, size=22, fill=INK, weight=400, anchor='start'):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}">{escape(value)}</text>')


def polygon(points, fill, stroke='none', width=1, extra=''):
    return f'<path d="{path(points)}" fill="{fill}" stroke="{stroke}" stroke-width="{width}" {extra}/>'


def frame(time):
    alignment = smooth((time - .8) / 2.7)
    rotation = .83 * (1 - alignment)
    offset = (60 * (1 - alignment), -10 * (1 - alignment))
    insertion = 85 * smooth((time - 4.) / 3.) + 75 * smooth((time - 8.5) / 2.8)
    tip_height = 30 - insertion
    peg_height = 185
    bottom_scale, top_scale = .47, 1.68
    crossing = bottom_scale + max(0, -tip_height) / peg_height * (top_scale - bottom_scale)
    phase = 0 if time < 4 else 1 if time < 8.5 else 2
    phases = ['Line up the shape', 'Press to a useful fit', 'Further in: poorer fit']
    # The plan-view panel illustrates the above-reference footprint, not the
    # literal peg silhouette before contact. Once aligned, only its level grows.
    footprint = .76 if phase == 0 else crossing
    components = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
                  f'viewBox="0 0 {WIDTH} {HEIGHT}"><rect width="1440" height="840" fill="{PAPER}"/>'
                  '<g font-family="DejaVu Sans, sans-serif">']
    components += [text(48, 59, 'A good fit needs both alignment and depth.', 36, weight=750),
                   text(48, 94, 'A shape-sorter analogy for individualized targeting and dose', 22, MUTED)]
    for index, title in enumerate(phases):
        x = 48 + index * 453
        selected = index == phase
        color = TEAL if index < 2 else ORANGE
        components.append(f'<rect x="{x}" y="122" width="438" height="61" rx="14" '
                          f'fill="{color if selected else "#e9e9e3"}"/>')
        components.append(text(x + 20, 160, f'{index + 1}  {title}', 21,
                               '#ffffff' if selected else MUTED, 700))
    components.append('<rect x="48" y="205" width="783" height="474" rx="22" fill="#eeece3"/>')
    components.append('<rect x="851" y="205" width="541" height="474" rx="22" fill="#ffffff"/>')
    components += [text(77, 245, 'THE TOY', 16, MUTED, 700),
                   text(881, 245, 'THE MODELED CORTICAL FOOTPRINT', 16, MUTED, 700)]
    # Wooden board, viewed obliquely. The hole stays fixed throughout.
    board = [(138, 474), (467, 364), (738, 514), (390, 641)]
    components.append(polygon([(138, 474), (390, 641), (390, 661), (138, 494)], '#c0a479'))
    components.append(polygon([(390, 641), (738, 514), (738, 534), (390, 661)], '#ae936c'))
    components.append(polygon(board, '#ddc69d', '#b89c70', 2))

    def project(points, height=0):
        return [(445 + x * 1.14 + y * .36, 501 + y * .26 - x * .08 - height) for x, y in points]

    hole = project(shape())
    components.append(polygon(hole, '#665744', '#897048', 9))
    components.append(polygon(project(shape(.93), -11), '#302e2b'))
    # Cross-section narrows toward the tip; pressing it down exposes a wider
    # section at the board, analogous to lowering E*/intensity in a fixed field.
    lower = project(shape(crossing if tip_height < 0 else bottom_scale, rotation, offset), max(0, tip_height))
    upper = project(shape(top_scale, rotation, offset), tip_height + peg_height)
    faces = []
    for index in range(len(lower)):
        other = (index + 1) % len(lower)
        light = .55 + .45 * ((math.sin(index * math.tau / len(lower) - .6) + 1) / 2)
        fill = f'rgb({int(21 + 32 * light)},{int(107 + 63 * light)},{int(114 + 63 * light)})'
        faces.append(((lower[index][1] + lower[other][1]) / 2,
                      polygon([lower[index], lower[other], upper[other], upper[index]], fill, fill, .6)))
    components.extend(svg for _, svg in sorted(faces))
    components.append(polygon(upper, '#69c8be', '#1f8e8d', 2))
    components.append(polygon(lower, 'none', '#107775', 2))
    # Outline front half of the hole: viewers can compare the peg at board level.
    if phase == 2:
        components.append(polygon(project(shape(crossing, rotation, offset)), 'none', ORANGE, 6,
                                  'stroke-dasharray="7 5"'))
    components += [text(86, 633, 'Fixed hole = target', 18, '#5c4c34', 700),
                   text(773, 298, 'Press', 17, MUTED, 700, 'middle'),
                   text(773, 321, 'depth', 17, MUTED, 700, 'middle')]
    components.append('<rect x="765" y="342" width="16" height="137" rx="8" fill="#d9d9d0"/>')
    components.append(f'<rect x="765" y="342" width="16" height="{8 + insertion / 160 * 129:.2f}" rx="8" fill="{TEAL if phase < 2 else ORANGE}"/>')
    components.append('<path d="M 753 493 L 773 511 L 793 493" fill="none" stroke="#596777" stroke-width="4"/>')
    # Plan view: target outline and contours of the SAME field after alignment.
    center = (1123, 414)
    target = [(center[0] + .82 * x, center[1] + .82 * y) for x, y in shape()]
    field = [(center[0] + .82 * x, center[1] + .82 * y) for x, y in shape(footprint, rotation, offset)]
    components.append('<defs><clipPath id="target">' + polygon(target, 'white') + '</clipPath>'
                      '<mask id="outside"><rect width="1440" height="840" fill="white"/>' + polygon(target, 'black') + '</mask></defs>')
    components.append(polygon(target, '#edf1df'))
    for size in (.47, .76, 1., 1.39):
        contour = [(center[0] + .82 * x, center[1] + .82 * y) for x, y in shape(size, rotation, offset)]
        components.append(polygon(contour, 'none', '#c8d8d7', 1.5, 'stroke-dasharray="4 5"'))
    components.append(polygon(field, TEAL, 'none', extra='clip-path="url(#target)" fill-opacity=".85"'))
    components.append(polygon(field, ORANGE, 'none', extra='mask="url(#outside)" fill-opacity=".9"'))
    components.append(polygon(target, 'none', INK, 3.5))
    components.append(polygon(field, 'none', '#176b70', 1.5))
    labels = [('Move + rotate the field.', 'Bring strong exposure onto the target.'),
              ('Same pose. Raise intensity.', 'More target lies above the reference.'),
              ('Same pose. Raise it further.', 'Off-target exposure also grows.')][phase]
    components += [text(1123, 586, labels[0], 22, INK, 700, 'middle'),
                   text(1123, 615, labels[1], 18, MUTED, 400, 'middle')]
    for x, color, label in [(890, TEAL, 'On-target'), (1130, ORANGE, 'Off-target')]:
        components.append(f'<rect x="{x}" y="640" width="15" height="15" rx="3" fill="{color}"/>')
        components.append(text(x + 23, 653, label, 17, MUTED))
    components += [text(48, 724, 'Pose sets where the field falls. Dose sets how much exceeds the reference.', 25, INK, 700),
                   text(48, 759, 'At a fixed pose, the field pattern stays the same; its above-reference footprint expands.', 21, MUTED),
                   text(48, 802, 'Conceptual analogy only — not a dose recommendation or a proven neuronal-activation threshold.', 17, MUTED)]
    components.append('</g></svg>')
    return ''.join(components)


def render(svg, destination):
    handle = Rsvg.Handle.new_from_data(svg.encode())
    canvas = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
    viewport = Rsvg.Rectangle()
    viewport.x, viewport.y, viewport.width, viewport.height = 0, 0, WIDTH, HEIGHT
    handle.render_document(cairo.Context(canvas), viewport)
    canvas.write_to_png(str(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    stem = 'targeting-dose-concept'
    poster = frame(7.6)
    (args.output / f'{stem}.svg').write_text(poster)
    render(poster, args.output / f'{stem}.png')
    with tempfile.TemporaryDirectory(prefix='tans-concept-frames-') as directory:
        frames = Path(directory)
        for index in range(FPS * DURATION):
            render(frame(index / FPS), frames / f'{index:04d}.png')
        subprocess.run(['ffmpeg', '-v', 'error', '-n', '-framerate', str(FPS),
                        '-i', str(frames / '%04d.png'), '-filter_complex',
                        '[0:v]split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4',
                        '-loop', '0', str(args.output / f'{stem}.gif')], check=True)
    print(args.output / f'{stem}.gif')


if __name__ == '__main__':
    main()
