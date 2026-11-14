// Draws orthogonal arrows between boxes inside a .stage element.
//   link('a', 'b', { from: 'right', to: 'left', color, label, dashed, both, fa, fb, mid, via, straight, lp })
// from/to: which edge of each box (left|right|top|bottom). fa/fb: position along that edge (0..1).
// mid: x (or y) of the elbow for same-axis links. via: explicit [[x,y],...] bend points.
// straight: snap the target point onto the source's line. lp: label position along the path (0..1).
const SVGNS = 'http://www.w3.org/2000/svg';

function stageSvg(stage) {
    let svg = stage.querySelector(':scope > svg.links');
    if (!svg) {
        svg = document.createElementNS(SVGNS, 'svg');
        svg.classList.add('links');
        svg.append(document.createElementNS(SVGNS, 'defs'));
        stage.prepend(svg);
    }
    svg.setAttribute('width', stage.clientWidth);
    svg.setAttribute('height', stage.clientHeight);
    return svg;
}

function marker(svg, color) {
    const id = 'm' + color.replace(/[^a-z0-9]/gi, '');
    if (!svg.querySelector('#' + id)) {
        const m = document.createElementNS(SVGNS, 'marker');
        Object.entries({ id, viewBox: '0 0 12 12', refX: 11, refY: 6, markerWidth: 12, markerHeight: 12,
                         markerUnits: 'userSpaceOnUse', orient: 'auto-start-reverse' }).forEach(([k, v]) => m.setAttribute(k, v));
        const p = document.createElementNS(SVGNS, 'path');
        p.setAttribute('d', 'M0,0.5 L12,6 L0,11.5 z');
        p.setAttribute('fill', color);
        m.append(p);
        svg.querySelector('defs').append(m);
    }
    return `url(#${id})`;
}

function cssColor(c) {
    if (!c) return getComputedStyle(document.documentElement).getPropertyValue('--line').trim();
    const m = /^var\((--[\w-]+)\)$/.exec(c);
    return m ? getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim() : c;
}

function link(a, b, o = {}) {
    const stage = document.getElementById(a).closest('.stage');
    const svg = stageSvg(stage);
    const s = stage.getBoundingClientRect();
    const box = id => {
        const r = document.getElementById(id).getBoundingClientRect();
        return { l: r.left - s.left, t: r.top - s.top, r: r.right - s.left, b: r.bottom - s.top, w: r.width, h: r.height };
    };
    const at = (bx, side, f) => ({
        left: [bx.l, bx.t + bx.h * f], right: [bx.r, bx.t + bx.h * f],
        top: [bx.l + bx.w * f, bx.t], bottom: [bx.l + bx.w * f, bx.b],
    })[side];
    const horiz = side => side === 'left' || side === 'right';
    const sa = o.from || 'right', sb = o.to || 'left';
    const p = at(box(a), sa, o.fa ?? 0.5), q = at(box(b), sb, o.fb ?? 0.5);
    if (o.straight) { if (horiz(sa)) q[1] = p[1]; else q[0] = p[0]; }

    let pts;
    if (o.via) pts = [p, ...o.via, q];
    else if (horiz(sa) && horiz(sb)) {
        const mx = o.mid ?? (p[0] + q[0]) / 2;
        pts = Math.abs(p[1] - q[1]) < 1 ? [p, q] : [p, [mx, p[1]], [mx, q[1]], q];
    } else if (!horiz(sa) && !horiz(sb)) {
        const my = o.mid ?? (p[1] + q[1]) / 2;
        pts = Math.abs(p[0] - q[0]) < 1 ? [p, q] : [p, [p[0], my], [q[0], my], q];
    } else pts = horiz(sa) ? [p, [q[0], p[1]], q] : [p, [p[0], q[1]], q];

    const color = cssColor(o.color);
    const path = document.createElementNS(SVGNS, 'path');
    path.setAttribute('d', pts.map((pt, i) => (i ? 'L' : 'M') + pt[0].toFixed(1) + ',' + pt[1].toFixed(1)).join(' '));
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', color);
    path.setAttribute('stroke-width', o.width ?? 2.5);
    path.setAttribute('stroke-linejoin', 'round');
    if (o.dashed) path.setAttribute('stroke-dasharray', '7 5');
    if (!o.noArrow) path.setAttribute('marker-end', marker(svg, color));
    if (o.both) path.setAttribute('marker-start', marker(svg, color));
    svg.append(path);

    if (o.label) {
        const len = path.getTotalLength();
        const c = path.getPointAtLength(len * (o.lp ?? 0.5));
        const g = document.createElementNS(SVGNS, 'g');
        const t = document.createElementNS(SVGNS, 'text');
        t.setAttribute('font-size', o.fontSize ?? 12.5);
        t.setAttribute('fill', color);
        t.setAttribute('font-weight', '600');
        const lines = String(o.label).split('\n');
        lines.forEach((line, i) => {
            const ts = document.createElementNS(SVGNS, 'tspan');
            ts.setAttribute('x', c.x + (o.dx ?? 0));
            ts.setAttribute('dy', i ? '1.2em' : 0);
            ts.setAttribute('text-anchor', 'middle');
            ts.textContent = line;
            t.append(ts);
        });
        t.setAttribute('y', c.y + (o.dy ?? 0) + 4 - (lines.length - 1) * 7.5);
        if (o.mono) t.classList.add('mono');
        g.append(t);
        svg.append(g);
        const bb = t.getBBox();
        const r = document.createElementNS(SVGNS, 'rect');
        Object.entries({ x: bb.x - 6, y: bb.y - 3, width: bb.width + 12, height: bb.height + 6, rx: 6,
                         fill: o.labelBg ?? '#f1f5f9', stroke: color, 'stroke-width': 1 }).forEach(([k, v]) => r.setAttribute(k, v));
        g.prepend(r);
    }
}
