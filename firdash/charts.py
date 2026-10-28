import html

SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
OTHER = "#898781"
CRIT, WARN, GOOD = "#d03b3b", "#fab219", "#0ca30c"


def esc(s):
    return html.escape(str(s))


def size(b):
    return f"{b / 2**40:.1f} TiB" if b >= 2**40 else f"{b / 2**30:.0f} GiB"


def count(n):
    return f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.0f}K" if n >= 1e4 else f"{n:,.0f}"


def nice_step(vmax, n=4):
    raw = max(vmax, 1) / n
    mag = 10 ** (len(str(int(raw))) - 1)
    return next(m * mag for m in (1, 2, 2.5, 3, 5, 10) if m * mag >= raw)


def status_fill(pct):
    return CRIT if pct >= 95 else WARN if pct >= 85 else SERIES[0]


def hatch_defs(colors):
    return "".join(
        f'<pattern id="h{col[1:]}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<rect width="6" height="6" fill="{col}" opacity="0.4"/><line x1="0" y1="0" x2="0" y2="6" stroke="{col}" stroke-width="2.5"/></pattern>'
        for col in colors)


def bars(rows, xmax, ticks, tick_fmt, ref=None, label_w=150, value_w=190, row_h=34, bar_h=20,
         sublabels=False, width=620):
    """rows: [(label, [(value, fill, tooltip, hatched, sublabel)], right_text)]"""
    plot_w = width - label_w - value_w
    gap = 18 if sublabels else 0
    h = len(rows) * (row_h + gap) + 28
    sx = lambda v: label_w + plot_w * min(v, xmax) / xmax
    out = [f'<svg viewBox="0 0 {width} {h}" width="100%" role="img">']
    for t in ticks:
        x = sx(t)
        out.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{h - 22}" class="grid"/>'
                   f'<text x="{x:.1f}" y="{h - 6}" class="tick" text-anchor="middle">{tick_fmt(t)}</text>')
    for i, (label, segs, right) in enumerate(rows):
        y = i * (row_h + gap) + (row_h - bar_h) / 2
        out.append(f'<text x="{label_w - 10}" y="{y + bar_h / 2 + 4:.1f}" class="lab" text-anchor="end">{esc(label)}</text>')
        out.append(f'<rect x="{label_w}" y="{y:.1f}" width="{plot_w}" height="{bar_h}" rx="4" class="track"/>')
        x0 = 0.0
        for v, fill, tip, hatched, sub in segs:
            x1, x2 = sx(x0), sx(x0 + v)
            w = max(x2 - x1 - 2, 1.5)
            pat = f'url(#h{fill[1:]})' if hatched else fill
            out.append(f'<rect x="{x1:.1f}" y="{y:.1f}" width="{w:.1f}" height="{bar_h}" rx="4" fill="{pat}">'
                       f'<title>{esc(tip)}</title></rect>')
            if sublabels and sub and w > 38:
                out.append(f'<text x="{x1 + w / 2:.1f}" y="{y + bar_h + 13:.1f}" class="sub" text-anchor="middle">{esc(sub)}</text>')
            x0 += v
        out.append(f'<text x="{label_w + plot_w + 10}" y="{y + bar_h / 2 + 4:.1f}" class="val">{right}</text>')
    if ref is not None:
        x = sx(ref)
        out.append(f'<line x1="{x:.1f}" y1="0" x2="{x:.1f}" y2="{h - 22}" class="ref"/>')
    out.append("</svg>")
    return "".join(out)


def line(xlabels, series, width=620, height=260):
    """series: [(name, color, [values])] sharing xlabels."""
    left, right, top, bottom = 40, 90, 12, 26
    pw, ph = width - left - right, height - top - bottom
    ymax = max([max(v) for _, _, v in series if v] + [1])
    step = max(1, round(ymax / 4))
    ymax = step * 4 if step * 4 >= ymax else step * 5
    n = max(len(xlabels) - 1, 1)
    sx = lambda i: left + pw * i / n
    sy = lambda v: top + ph * (1 - v / ymax)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in range(0, ymax + 1, step):
        out.append(f'<line x1="{left}" y1="{sy(t):.1f}" x2="{left + pw}" y2="{sy(t):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{sy(t) + 4:.1f}" class="tick" text-anchor="end">{t}</text>')
    every = max(1, len(xlabels) // 7)
    for i, lab in enumerate(xlabels):
        if i % every == 0 or i == len(xlabels) - 1:
            out.append(f'<text x="{sx(i):.1f}" y="{height - 6}" class="tick" text-anchor="middle">{esc(lab)}</text>')
    for name, col, vals in series:
        pts = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(vals))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round"/>')
        out.extend(f'<circle cx="{sx(i):.1f}" cy="{sy(v):.1f}" r="4" fill="{col}" class="dot"/>'
                   for i, v in enumerate(vals) if v)
        if vals:
            out.append(f'<text x="{sx(len(vals) - 1) + 8:.1f}" y="{sy(vals[-1]) + 4:.1f}" class="val">{esc(name)} {vals[-1]}</text>')
    for i, lab in enumerate(xlabels):
        tip = " · ".join(f"{name}: {vals[i]}" for name, _, vals in series)
        out.append(f'<rect x="{sx(i) - pw / n / 2:.1f}" y="{top}" width="{pw / n:.1f}" height="{ph}" class="hover">'
                   f'<title>{esc(lab)} — {esc(tip)}</title></rect>')
    out.append(f'<line x1="{left}" y1="{top + ph}" x2="{left + pw}" y2="{top + ph}" class="axis"/></svg>')
    return "".join(out)


def legend(items, tape=False):
    out = [f'<span><i style="background:{col}"></i>{esc(name)}</span>' for name, col in items]
    if tape:
        out.append('<span><i class="hatch"></i>tape-only</span>')
    return f'<div class="legend">{"".join(out)}</div>'


def columns(xlabels, series, width=620, height=260):
    """Stacked columns. series: [(name, color, [values])] sharing xlabels."""
    left, right, top, bottom = 40, 10, 12, 26
    pw, ph = width - left - right, height - top - bottom
    totals = [sum(s[2][i] for s in series) for i in range(len(xlabels))]
    step = max(1, int(nice_step(max(totals + [1]))))
    ymax = step * -(-max(totals + [1]) // step)
    slot = pw / max(len(xlabels), 1)
    bw = max(min(slot - 4, 28), 2)
    sy = lambda v: top + ph * (1 - v / ymax)
    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img">']
    for t in range(0, ymax + 1, step):
        out.append(f'<line x1="{left}" y1="{sy(t):.1f}" x2="{left + pw}" y2="{sy(t):.1f}" class="grid"/>'
                   f'<text x="{left - 6}" y="{sy(t) + 4:.1f}" class="tick" text-anchor="end">{t}</text>')
    every = max(1, len(xlabels) // 7)
    for i, lab in enumerate(xlabels):
        cx = left + slot * (i + 0.5)
        if (len(xlabels) - 1 - i) % every == 0:
            out.append(f'<text x="{cx:.1f}" y="{height - 6}" class="tick" text-anchor="middle">{esc(lab)}</text>')
        v0 = 0
        for name, col, vals in series:
            v = vals[i]
            if v:
                y1, y2 = sy(v0 + v), sy(v0)
                out.append(f'<rect x="{cx - bw / 2:.1f}" y="{y1:.1f}" width="{bw:.1f}" height="{max(y2 - y1 - 2, 1):.1f}" rx="3" fill="{col}"/>')
            v0 += v
        tip = " · ".join(f"{name}: {vals[i]}" for name, _, vals in series)
        out.append(f'<rect x="{cx - slot / 2:.1f}" y="{top}" width="{slot:.1f}" height="{ph}" class="hover"><title>{esc(lab)} — {esc(tip)}</title></rect>')
    out.append(f'<line x1="{left}" y1="{top + ph}" x2="{left + pw}" y2="{top + ph}" class="axis"/></svg>')
    return "".join(out)
