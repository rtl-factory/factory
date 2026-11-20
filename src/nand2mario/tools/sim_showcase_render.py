#!/usr/bin/env python3
"""Render the nand2mario README simulation showcase from game-qualification frames.

Input: the per-game results directory written by the n2m_system game
qualification (test_system_games, issue #395). Output: 2x PNGs, a sampled GIF
per game, a title strip and provenance.json. Simulation captures only.
"""
import argparse, hashlib, json, os
from PIL import Image

GAMES = [("max-pirate", "Max Pirate", "Marcel Wehrstedt",
          "GB Oracles artwork by GibbonGL; audio by beatscribe"),
         ("square-fall", "Square Fall", "bjorn_nah (Bjorn)", ""),
         ("unstoppable-knight", "Unstoppable Knight", "Rafagars (Rafael Garcia)",
          "includes an edited Twilight Drive track")]
STEPS = ["title", "start", "release", "play", "right"]

def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, action="append",
                    help="qualification results directory (repeatable; first match wins)")
    ap.add_argument("--catalog", help="JSON list of {slug,name,author,license,credits}; default: the three qualified games")
    ap.add_argument("--output", required=True)
    ap.add_argument("--source-commit", required=True)
    a = ap.parse_args(argv)
    os.makedirs(a.output, exist_ok=True)
    prov = {"schema_version": 1,
            "label": "Verilator n2m_system simulation frame capture; not a physical board or VGA capture",
            "source_commit": a.source_commit, "seed": 42,
            "scale": "2x nearest-neighbour from 160x144 4-shade grayscale",
            "playback": "5 input-step samples per game (title/start/release/play/right), 1000 ms each, identical consecutive samples merged; editorial samples, not measured FPS",
            "pillow_version": Image.__version__, "games": []}
    games = ([(c["slug"], c["name"], c["author"], c.get("credits", ""), c["license"], c.get("gallery_step", "play"))
              for c in json.load(open(a.catalog))] if a.catalog
             else [(*g, "MIT", "play") for g in GAMES])
    tiles, gallery = [], []
    for g, name, author, credits, lic, gstep in games:
        base = next((r for r in a.results if os.path.exists(f"{r}/{g}/game-result.json")), None)
        if base is None:
            raise FileNotFoundError(f"no results for {g}")
        res = json.load(open(f"{base}/{g}/game-result.json"))
        frames, ims = [], []
        for s in STEPS:
            srcp = f"{base}/{g}/{s}.png"
            im = Image.open(srcp).convert("L").resize((320, 288), Image.NEAREST)
            p = f"{a.output}/{g}-{s}.png"
            im.save(p)
            ims.append(im)
            frames.append({"png": os.path.basename(p), "step": s,
                           "source_png_sha256": sha(srcp), "sha256": sha(p)})
        gp = f"{a.output}/{g}.gif"
        pal = Image.new("P", (1, 1))
        pal.putpalette([v for c in (0, 85, 170, 255) for v in (c, c, c)])
        uniq, durs = [], []
        for im in ims:  # identical consecutive samples become one longer frame
            if uniq and uniq[-1].tobytes() == im.tobytes():
                durs[-1] += 1000
            else:
                uniq.append(im)
                durs.append(1000)
        pims = [im.convert("RGB").quantize(palette=pal, dither=Image.Dither.NONE)
                for im in uniq]
        pims[0].save(gp, save_all=True, append_images=pims[1:], duration=durs,
                     loop=0, optimize=False, disposal=2)
        gif = Image.open(gp)
        assert gif.n_frames == len(uniq), (g, gif.n_frames)
        for i, im in enumerate(uniq):
            gif.seek(i)
            assert gif.convert("L").tobytes() == im.tobytes(), (g, i)
        tiles.append(ims[0])
        gallery.append(ims[STEPS.index(gstep)])
        prov["games"].append({"game": name, "author": author, "license": lic,
                              "upstream_credits": credits,
                              "rom_sha256": res.get("image_sha256"),
                              "gif": os.path.basename(gp), "gif_sha256": sha(gp),
                              "frames": frames})
    strip = Image.new("L", (320 * 3 + 16, 288), 255)
    for i, t in enumerate(tiles[:3]):
        strip.paste(t, (i * 328, 0))
    sp = f"{a.output}/games-title-strip.png"
    strip.save(sp)
    prov["title_strip"] = {"png": "games-title-strip.png", "games": [g[1] for g in games[:3]], "sha256": sha(sp)}
    cols = 3
    rows = (len(gallery) + cols - 1) // cols
    grid = Image.new("L", (cols * 328 - 8, rows * 296 - 8), 255)
    for i, t in enumerate(gallery):
        grid.paste(t, ((i % cols) * 328, (i // cols) * 296))
    gp2 = f"{a.output}/games-gallery.png"
    grid.save(gp2)
    prov["gallery"] = {"png": "games-gallery.png", "steps": {g[1]: g[5] for g in games}, "games": [g[1] for g in games], "sha256": sha(gp2)}
    json.dump(prov, open(f"{a.output}/provenance.json", "w"), indent=2)
    open(f"{a.output}/provenance.json", "a").write("\n")

if __name__ == "__main__":
    main()
