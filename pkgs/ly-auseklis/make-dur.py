"""Write auseklis.dur: the CM/26 monogram resolving coarse -> sharp, for Ly.

The .dur format is durdraw's: gzipped JSON, every frame a list of text rows
plus colorMap[x][y] = [fg, bg]. Ly plays frames at `framerate` and holds each
for an extra `delay` seconds, then loops, so the sharp frame carries the long
delay and the resolve replays every ~45s.

Colours use the 256 format with full_color on: Ly sends them as 24-bit and
the kernel console snaps each to its 16-slot palette by brightness
(vt.c rgb_foreground), which Stylix's console target remaps to Auseklis. The
indices below are the ones that land on the intended slots:
  15  (255,255,255) -> bright white  -> #f4f7fc  letters
  12  (0,0,255)     -> bright blue   -> #3b6bff  slash + shadow
  236 (48,48,48)    -> dark grey     -> #3a4152  readout
"""

import gzip
import json
import sys

import monogram as m

WHITE, COBALT, SLATE, GROUND = 15, 12, 236, 0
COLOUR = {m.LETTER: WHITE, m.ACCENT: COBALT}
SIGNATURE = "CM/2026"  # Portfolio2's signature, right-aligned on the readout row
FPS = 12  # ~83 ms a step, Portfolio2's resolve cadence
HOLD = 45  # seconds the sharp frame stays up before the next resolve


def frame(step):
    pix, col = m.resolve(*m.bitmap(), step)
    cells = m.cells(pix, col)
    width = len(cells[0])
    label = "RES / OK" if step == 1 else f"RES / {step:02d}"
    rows = [[(ch, COLOUR[role] if ch != " " else GROUND) for ch, role in r] for r in cells]
    rows.append([(" ", GROUND)] * width)
    readout = [(c, SLATE) for c in label.ljust(width - len(SIGNATURE))]
    sig = [(c, COBALT if c == "/" else WHITE) for c in SIGNATURE]
    rows.append(readout + sig)
    return rows


def main(out):
    frames = []
    steps = m.STEPS
    for i, step in enumerate(steps):
        rows = frame(step)
        h, w = len(rows), len(rows[0])
        frames.append({
            "frameNumber": i + 1,
            "delay": HOLD if step == 1 else 0,
            "contents": ["".join(ch for ch, _ in r) for r in rows],
            "colorMap": [[[rows[y][x][1], GROUND] for y in range(h)] for x in range(w)],
        })
    movie = {
        "DurMovie": {
            "formatVersion": 7,
            "colorFormat": "256",
            "preferredFont": "fixed",
            "encoding": "utf-8",
            "name": "auseklis",
            "artist": "perkons",
            "framerate": float(FPS),
            "sizeX": len(frames[0]["contents"][0]),
            "sizeY": len(frames[0]["contents"]),
            "extra": None,
            "frames": frames,
        }
    }
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(movie, f)


if __name__ == "__main__":
    main(sys.argv[1])
