"""Assemble the demo video: clips + stills (demo/record.py) + narration wavs → one mp4.

    python demo/assemble.py --clips <dir with slide_*.png, tab_*.webm, events.json> \
                            --tts <dir with S1.wav … S8.wav> --out demo.mp4

Rules (see demo/narration.md): each segment lasts exactly as long as its narration (+ a short
pause). A still is held; a tab clip is cut to fit — model waits are shortened first (never the
clicks or the result), a failed-and-retried click is removed entirely, and if the clip is still
too long it is sped up, if too short its last frame is held. Only ffmpeg is needed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess

SEGMENTS = [  # (segment id, visual)
    ("S1", "slide_title"), ("S2", "slide_problem"), ("S3", "tab_check"), ("S4", "tab_refund"),
    ("S5", "tab_notes"), ("S6", "tab_listing"), ("S7", "slide_arch"), ("S8", "slide_close"),
]
PAUSE = 0.6        # seconds of silence after each narration
MIN_WAIT = 2.0     # a model wait is never shortened below this (the "agent running…" state stays visible)
FPS = 30


def dur(path: pathlib.Path) -> float:
    o = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(o.strip())


def keep_ranges(events: list[dict], clip_len: float, target: float) -> list[tuple[float, float]]:
    """Return the [start, end) ranges of the clip to keep so that the total is close to target."""
    cuts: list[tuple[float, float, bool]] = []  # (start, end, is_wait)
    ev = [e for e in events if e["kind"] in ("click", "error", "result")]
    for i, e in enumerate(ev):
        if e["kind"] != "click":
            continue
        nxt = ev[i + 1] if i + 1 < len(ev) else None
        if nxt is None:
            continue
        if nxt["kind"] == "error":  # failed attempt: drop from this click to the next click
            j = next((k for k in range(i + 1, len(ev)) if ev[k]["kind"] == "click"), None)
            if j is not None:
                cuts.append((e["t"] - 0.05, ev[j]["t"] - 0.05, False))
        elif nxt["kind"] == "result" and nxt["t"] - e["t"] > MIN_WAIT + 1.0:
            cuts.append((e["t"] + 1.0, nxt["t"] - 0.6, True))
    # how much wait to keep: scale between "all waits cut to MIN_WAIT" and "nothing cut"
    fixed_cut = sum(b - a for a, b, w in cuts if not w)
    waits = [(a, b) for a, b, w in cuts if w]
    full = clip_len - fixed_cut
    minimal = full - sum(max(0.0, (b - a) - MIN_WAIT) for a, b in waits)
    if not waits or target >= full:
        frac = 1.0
    elif target <= minimal:
        frac = 0.0
    else:
        frac = (target - minimal) / (full - minimal)
    final: list[tuple[float, float]] = [(a, b) for a, b, w in cuts if not w]
    for a, b in waits:
        keep = MIN_WAIT + frac * ((b - a) - MIN_WAIT)
        final.append((a + keep, b))
    final.sort()
    ranges, pos = [], 0.0
    for a, b in final:
        if a > pos:
            ranges.append((pos, a))
        pos = max(pos, b)
    if pos < clip_len:
        ranges.append((pos, clip_len))
    return ranges


def build_segment(sid: str, visual: str, clips: pathlib.Path, tts: pathlib.Path, work: pathlib.Path, events: dict) -> pathlib.Path:
    audio = tts / f"{sid}.wav"
    target = dur(audio) + PAUSE
    out = work / f"{sid}.mp4"
    common = ["-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
              "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-t", f"{target:.3f}", "-y", str(out)]
    if visual.startswith("slide_"):
        cmd = ["ffmpeg", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-i", str(clips / f"{visual}.png"),
               "-i", str(audio), "-af", f"apad=pad_dur={PAUSE}", "-vf", "scale=1920:1080,format=yuv420p", *common]
        subprocess.check_call(cmd)
        return out
    clip = clips / f"{visual}.webm"
    clip_len = dur(clip)
    ranges = keep_ranges(events[visual]["events"], clip_len, target)
    kept = sum(b - a for a, b in ranges)
    parts = "".join(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}];" for i, (a, b) in enumerate(ranges))
    chain = parts + "".join(f"[v{i}]" for i in range(len(ranges))) + f"concat=n={len(ranges)}:v=1:a=0[vc];"
    if kept < target - 0.05:
        chain += f"[vc]tpad=stop_mode=clone:stop_duration={target - kept:.3f}[vo]"
    elif kept > target + 0.05:
        chain += f"[vc]setpts=PTS*{target / kept:.5f}[vo]"
    else:
        chain += "[vc]copy[vo]"
    chain += f";[1:a]apad=pad_dur={PAUSE}[ao]"
    cmd = ["ffmpeg", "-v", "error", "-i", str(clip), "-i", str(audio), "-filter_complex", chain,
           "-map", "[vo]", "-map", "[ao]", *common]
    subprocess.check_call(cmd)
    print(f"  {sid} {visual}: clip {clip_len:.1f}s → kept {kept:.1f}s, target {target:.1f}s, ranges {[(round(a,1), round(b,1)) for a, b in ranges]}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", required=True)
    ap.add_argument("--tts", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    clips, tts, out = pathlib.Path(a.clips), pathlib.Path(a.tts), pathlib.Path(a.out)
    work = out.parent / "_segments"
    work.mkdir(parents=True, exist_ok=True)
    events = json.loads((clips / "events.json").read_text())
    parts = [build_segment(sid, vis, clips, tts, work, events) for sid, vis in SEGMENTS]
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    subprocess.check_call(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", "-y", str(out)])
    print(f"wrote {out} — {dur(out):.1f} s")


if __name__ == "__main__":
    main()
