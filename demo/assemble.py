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

SEGMENTS = [  # (segment id, visual) — a list of stills is shown in equal shares of the narration
    ("S1", "slide_title"), ("S2", ["slide_problem_1", "slide_problem_2", "slide_problem_3", "slide_problem_4"]),
    ("S3", "tab_check"), ("S4", "tab_refund"), ("S5", "tab_notes"), ("S6", "tab_listing"),
    ("S7", ["slide_arch_1", "slide_arch_2", "slide_arch_3", "slide_arch_4"]), ("S8", "slide_close"),
]
PAUSE = 0.6        # seconds of silence after each narration
MIN_WAIT = 2.5     # every model wait is cut to this (enough to show the "agent running…" status)
MAX_STRETCH = 2.0  # the result range may be slowed down up to this factor before the last frame is held
FPS = 30


def dur(path: pathlib.Path) -> float:
    o = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(o.strip())


def keep_ranges(events: list[dict], clip_len: float) -> list[tuple[float, float]]:
    """Return the [start, end) ranges of the clip to keep (retries removed, waits cut to MIN_WAIT)."""
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
    # every wait is cut to MIN_WAIT: the result must be on screen within seconds of the click;
    # the remaining narration time is filled by stretching/holding the result (see build_segment)
    final: list[tuple[float, float]] = [(a, b) for a, b, w in cuts if not w]
    for a, b, w in cuts:
        if w:
            final.append((a + MIN_WAIT, b))
    final.sort()
    ranges, pos = [], 0.0
    for a, b in final:
        if a > pos:
            ranges.append((pos, a))
        pos = max(pos, b)
    if pos < clip_len:
        ranges.append((pos, clip_len))
    return ranges


def build_segment(sid: str, visual, clips: pathlib.Path, tts: pathlib.Path, work: pathlib.Path, events: dict) -> pathlib.Path:
    audio = tts / f"{sid}.wav"
    target = dur(audio) + PAUSE
    out = work / f"{sid}.mp4"
    common = ["-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
              "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-t", f"{target:.3f}", "-y", str(out)]
    if isinstance(visual, list) or visual.startswith("slide_"):
        stills = visual if isinstance(visual, list) else [visual]
        share = target / len(stills)
        ins = []
        for st in stills:
            ins += ["-loop", "1", "-framerate", str(FPS), "-t", f"{share:.3f}", "-i", str(clips / f"{st}.png")]
        n = len(stills)
        vf = "".join(f"[{i}:v]scale=1920:1080,format=yuv420p,setpts=PTS-STARTPTS[s{i}];" for i in range(n))
        vf += "".join(f"[s{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[vo];[{n}:a]apad=pad_dur={PAUSE}[ao]"
        cmd = ["ffmpeg", "-v", "error", *ins, "-i", str(audio), "-filter_complex", vf, "-map", "[vo]", "-map", "[ao]", *common]
        subprocess.check_call(cmd)
        return out
    clip = clips / f"{visual}.webm"
    clip_len = dur(clip)
    ranges = keep_ranges(events[visual]["events"], clip_len)
    kept = sum(b - a for a, b in ranges)
    # stretch factor for the last range (the result on screen), capped; the rest is held
    last_len = ranges[-1][1] - ranges[-1][0]
    head = kept - last_len
    k = 1.0
    if kept < target - 0.05:
        k = min(MAX_STRETCH, (target - head) / last_len)
    elif kept > target + 0.05:
        k = max(0.5, (target - head) / last_len)  # shrink the result range if the clip is too long
    parts = ""
    for i, (a, b) in enumerate(ranges):
        f = f"setpts=(PTS-STARTPTS)*{k:.5f}" if i == len(ranges) - 1 else "setpts=PTS-STARTPTS"
        parts += f"[0:v]trim=start={a:.3f}:end={b:.3f},{f}[v{i}];"
    chain = parts + "".join(f"[v{i}]" for i in range(len(ranges))) + f"concat=n={len(ranges)}:v=1:a=0[vc];"
    shown = head + last_len * k
    if shown < target - 0.05:
        chain += f"[vc]tpad=stop_mode=clone:stop_duration={target - shown:.3f}[vo]"
    else:
        chain += "[vc]copy[vo]"
    chain += f";[1:a]apad=pad_dur={PAUSE}[ao]"
    cmd = ["ffmpeg", "-v", "error", "-i", str(clip), "-i", str(audio), "-filter_complex", chain,
           "-map", "[vo]", "-map", "[ao]", *common]
    subprocess.check_call(cmd)
    print(f"  {sid} {visual}: clip {clip_len:.1f}s → kept {kept:.1f}s (tail ×{k:.2f}), target {target:.1f}s, ranges {[(round(a,1), round(b,1)) for a, b in ranges]}")
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
