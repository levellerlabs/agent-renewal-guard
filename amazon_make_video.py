#!/usr/bin/env python3
"""Agent Renewal Guard - Amazon Developer Hackathon demo video builder.

Field-notebook style per Joe's review bar (proven on WebMCP v2 + AFH v2):
paper/ink identity, REAL terminal transcripts + REAL console screenshots,
tight pacing (target 75-90s), edge-tts en-GB-RyanNeural at +15%.

Segments:
  title     notebook card
  problem   notebook card
  how       notebook card (the privilege split IS the product)
  proto     terminal: initialize 2025-11-25 + tools/list (typing)
  scan      terminal: list_upcoming_renewals + get_renewal_detail (typing)
  propose   terminal: propose_decision -> tray (typing)
  guard     terminal: record_decision REFUSED + tray approves (typing)
  console   REAL console screenshots (5 B-roll frames, pinned to paper)
  design    notebook card
  outro     notebook card

Run:  strands-venv python amazon_make_video.py   (heavy: overnight window)
"""
import json
import subprocess
import shutil
import asyncio
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).parent
WORK = HERE / "video_work"
TRANSCRIPTS = WORK / "transcripts"
SHOTS = HERE / "shots"
OUT = HERE / "agent-renewal-guard-demo.mp4"

W, H = 1280, 800
PAPER = (246, 241, 230)
PAPER2 = (239, 232, 216)
INK = (36, 33, 28)
MUTED = (122, 114, 100)
RULE = (201, 191, 168)
RED = (140, 47, 29)
GREEN = (31, 77, 58)

SERIF = "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
SERIF_B = "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

NARRATION = {
    "title": "Agent Renewal Guard - an M-C-P server that lets a voice agent manage recurring payments without ever letting it move money.",
    "problem": "Voice agents want to help with subscriptions. But the obvious design - let the agent cancel and pay - is the one nobody should ship.",
    "how": "So the privilege split is the product. The agent reads renewals and records proposals. The approval tray holds the only key that records decisions. Not policy - toolset.",
    "proto": "The server speaks M-C-P over Streamable HTTP, protocol twenty-twenty-five-eleven-twenty-five - the current spec, verified live with a real client handshake.",
    "scan": "The agent gets facts, not vibes: five renewals in the window, each with monthly cost, due date and a utilisation signal it can cite out loud.",
    "propose": "Its one write path is a proposal. Cancel the unused gym membership - near four hundred pounds a year - pending a human.",
    "guard": "Now the guardrail. The agent tries to record a decision - and is refused, client-side, because the tool was never granted. The tray approves it. The human decided.",
    "console": "The simulated Alexa-plus console: both panes talk to the live server. Left - the briefing agent, least-privilege. Right - the approval tray. Approve or reject, the ledger updates.",
    "design": "Three choices. Least privilege at the client, not the prompt. Proposals, never actions. And a skill file an orchestrator can follow.",
    "outro": "The agent proposes. The human decides. M-I-T licensed, self-hosted, one file to run.",
}


def tts():
    import edge_tts

    async def gen():
        for key, text in NARRATION.items():
            p = WORK / f"tts_{key}.mp3"
            if not p.exists():
                await edge_tts.Communicate(text, "en-GB-RyanNeural", rate="+18%").save(str(p))

    asyncio.run(gen())
    d = {}
    for key in NARRATION:
        p = WORK / f"tts_{key}.mp3"
        dur = float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(p)]).strip())
        d[key] = (str(p), dur)
        print(f"tts {key}: {dur:.1f}s")
    return d


def fnt(path, size):
    return ImageFont.truetype(path, size)


def canvas():
    img = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(img)
    for y in range(80, H, 26):
        d.line([(0, y), (W, y)], fill=(140, 131, 108, 20), width=1)
    d.line([(64, 0), (64, H)], fill=(200, 160, 140), width=2)
    return img, d


def caption_bar(img, d, text):
    d.rectangle([(0, H - 52), (W, H)], fill=INK)
    d.text((28, H - 38), text, font=fnt(MONO, 19), fill=PAPER)


def header(d, small):
    d.text((84, 26), small.upper(), font=fnt(MONO, 15), fill=MUTED)


def notebook_card(title, bullets, caption, foot=None):
    img, d = canvas()
    header(d, "agent renewal guard - field notes")
    d.text((84, 70), title, font=fnt(SERIF_B, 40), fill=INK)
    d.line([(84, 124), (W - 100, 124)], fill=RULE, width=2)
    y = 158
    for b in bullets:
        if b.startswith("*"):
            d.ellipse([(92, y + 10), (102, y + 20)], outline=RED, width=2)
            d.text((120, y), b[1:].strip(), font=fnt(SERIF_B, 24), fill=INK)
        else:
            d.text((120, y), b, font=fnt(SERIF, 24), fill=(60, 55, 45))
        y += 52
    if foot:
        d.text((84, H - 92), foot, font=fnt(MONO, 16), fill=MUTED)
    caption_bar(img, d, caption)
    return img


def terminal_frame(lines_visible, caption, title="leveller@edge:~/agent-renewal-guard"):
    img, d = canvas()
    header(d, "captured transcript - real run")
    x0, y0, x1, y1 = 100, 66, W - 72, H - 86
    d.polygon([(x0, y0), (x0 + 70, y0), (x0, y0 + 24)], fill=RULE)
    d.polygon([(x1, y1), (x1 - 70, y1), (x1, y1 - 24)], fill=RULE)
    d.rectangle([x0, y0, x1, y1], fill=(13, 17, 26))
    d.rectangle([x0, y0, x1, y1], outline=(60, 52, 40), width=2)
    d.rectangle([x0, y0, x1, y0 + 34], fill=(23, 30, 46))
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([(x0 + 16 + i * 22, y0 + 10), (x0 + 30 + i * 22, y0 + 24)], fill=c)
    d.text((x0 + 90, y0 + 8), title, font=fnt(MONO, 15), fill=(143, 160, 189))
    fm, fmb = fnt(MONO, 17), fnt(MONO_B, 17)
    y = y0 + 48
    for ln in lines_visible:
        s = ln.strip()
        if not s:
            y += 22
            continue
        if s.startswith("#"):
            d.text((x0 + 18, y), ln, font=fmb, fill=(255, 180, 84))
        elif s.startswith("[agent]"):
            d.text((x0 + 18, y), ln, font=fmb, fill=(126, 211, 135))
        elif s.startswith("[proposal]") or s.startswith("[tray]") or s.startswith("[ok]"):
            d.text((x0 + 18, y), ln, font=fmb, fill=(97, 200, 255))
        elif s.startswith("[scan]") or s.startswith("[detail]"):
            d.text((x0 + 18, y), ln, font=fm, fill=(97, 138, 255))
        elif s.startswith("[init]"):
            d.text((x0 + 18, y), ln, font=fm, fill=(220, 226, 235))
        elif s.startswith("<") and ("REFUSED" in s or "ERROR" in s):
            d.text((x0 + 18, y), ln, font=fmb, fill=(255, 120, 120))
        else:
            d.text((x0 + 18, y), ln, font=fm, fill=(220, 226, 235))
        y += 24
    caption_bar(img, d, caption)
    return img


def shot_frame(shot_path, caption, note):
    """REAL console screenshot pinned to the notebook page."""
    img, d = canvas()
    header(d, "simulated alexa+ console - live capture")
    shot = Image.open(shot_path).convert("RGB")
    # fit into the page area with margin
    max_w, max_h = W - 260, H - 240
    scale = min(max_w / shot.width, max_h / shot.height)
    nw, nh = int(shot.width * scale), int(shot.height * scale)
    shot = shot.resize((nw, nh), Image.Resampling.LANCZOS)
    x0, y0 = (W - nw) // 2, 96
    # paper shadow + pin tape
    d.rectangle([x0 - 8, y0 - 8, x0 + nw + 8, y0 + nh + 8], fill=PAPER2, outline=RULE, width=2)
    d.polygon([(x0, y0), (x0 + 70, y0), (x0, y0 + 24)], fill=RULE)
    d.polygon([(x0 + nw, y0 + nh), (x0 + nw - 70, y0 + nh), (x0 + nw, y0 + nh - 24)], fill=RULE)
    img.paste(shot, (x0, y0))
    d.text((84, H - 92), note, font=fnt(MONO, 16), fill=MUTED)
    caption_bar(img, d, caption)
    return img


def load(name):
    p = TRANSCRIPTS / f"{name}.txt"
    return p.read_text().splitlines() if p.exists() else [f"# {name} transcript missing"]


def encode(imgs, key, tts_path, fps=24, pad_tail=0.5):
    dur = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", tts_path]).strip())
    total = dur + pad_tail
    frames = int(total * fps)
    imgdir = WORK / f"frames_{key}"
    if imgdir.exists():
        shutil.rmtree(imgdir)
    imgdir.mkdir(parents=True)
    for i in range(frames):
        t = i / fps
        idx = min(int((t / total) * len(imgs)), len(imgs) - 1)
        imgs[idx].save(imgdir / f"f{i:05d}.png")
    out = WORK / f"seg_{key}.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps),
                    "-i", str(imgdir / "f%05d.png"), "-i", tts_path,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "128k", "-t", f"{total:.2f}", str(out)], check=True)
    shutil.rmtree(imgdir)
    print(f"seg {key}: {total:.1f}s")
    return out


def typing_segment(lines, key, caption, tts_path, fps=24):
    dur = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", tts_path]).strip())
    total = dur + 0.5
    frames = int(total * fps)
    total_chars = sum(len(l) + 1 for l in lines)
    imgdir = WORK / f"frames_{key}"
    if imgdir.exists():
        shutil.rmtree(imgdir)
    imgdir.mkdir(parents=True)
    for i in range(frames):
        frac = min((i / fps) / (total * 0.8), 1.0)
        show = int(frac * total_chars)
        vis, used = [], 0
        for ln in lines:
            if used >= show:
                break
            room = show - used
            vis.append(ln[:room])
            used += len(ln) + 1
        terminal_frame(vis, caption).save(imgdir / f"f{i:05d}.png")
    out = WORK / f"seg_{key}.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(fps),
                    "-i", str(imgdir / "f%05d.png"), "-i", tts_path,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "128k", "-t", f"{total:.2f}", str(out)], check=True)
    shutil.rmtree(imgdir)
    print(f"seg {key}: {total:.1f}s (typing)")
    return out


def main():
    WORK.mkdir(exist_ok=True)
    ttsd = tts()
    segs = []

    segs.append(encode(
        [notebook_card("Agent Renewal Guard", [
            "*a self-hosted MCP server",
            "for voice agents managing recurring payments",
            "Alexa+ track - Amazon Developer Hackathon",
        ], "the privilege split is the product", foot="field notes - build ship shape")],
        "title", ttsd["title"][0], pad_tail=0.4))

    segs.append(encode(
        [notebook_card("The problem", [
            "voice agents want to help with subscriptions",
            "",
            "*the obvious design lets the agent cancel and pay",
            "*that is exactly the design nobody should ship",
        ], "nobody should ship an agent that can move money")],
        "problem", ttsd["problem"][0], pad_tail=0.4))

    segs.append(encode(
        [notebook_card("The split is the product", [
            "*agent: read facts, record proposals",
            "human tray: the ONLY holder of record_decision",
            "",
            "not policy - toolset",
        ], "least privilege at the client, not the prompt")],
        "how", ttsd["how"][0], pad_tail=0.4))

    bl = load("briefing_raw")
    proto = [l for l in bl if "initialize" in l or "protocolVersion" in l
             or "tools/list" in l or "list_upcoming" in l
             or l.startswith("< list_") or "record_decision NOT" in l]
    segs.append(typing_segment(proto, "proto",
                 "initialize + tools/list - MCP 2025-11-25, least privilege",
                 ttsd["proto"][0]))

    scan = [l for l in bl if l.startswith("[scan]") or l.startswith("[agent] list_upcoming")
            or l.startswith("[detail]") or "< " in l and "FlexFitness" in l]
    segs.append(typing_segment(scan, "scan",
                 "list_upcoming_renewals - facts the agent can cite aloud",
                 ttsd["scan"][0]))

    prop = [l for l in bl if "propose_decision" in l or l.startswith("[proposal]")]
    segs.append(typing_segment(prop, "propose",
                 "propose_decision - one write path, pending a human",
                 ttsd["propose"][0]))

    gu = load("guardrail_raw")
    segs.append(typing_segment(gu, "guard",
                 "guardrail: agent refused, tray approves",
                 ttsd["guard"][0]))

    shots = [SHOTS / "ui_1_briefing.png", SHOTS / "ui_2_tray.png",
             SHOTS / "ui_3_guardrail.png", SHOTS / "ui_4_approved.png"]
    notes = ["voice briefing pane - advising tools only",
             "approval tray - the only record_decision client",
             "guardrail: agent attempt refused client-side",
             "approved - ledger updated, no money moved"]
    imgs = [shot_frame(s, "simulated alexa+ console - real browser capture", n)
            for s, n in zip(shots, notes)]
    segs.append(encode(imgs, "console", ttsd["console"][0], pad_tail=0.5))

    segs.append(encode(
        [notebook_card("Three deliberate choices", [
            "*least privilege at the client, not the prompt",
            "*proposals, never actions",
            "*an agent skill file the orchestrator can follow",
        ], "records over prose - proposals over actions")],
        "design", ttsd["design"][0], pad_tail=0.4))

    segs.append(encode(
        [notebook_card("Agent Renewal Guard", [
            "*the agent proposes, the human decides",
            "MIT licensed - self-hosted - one file to run",
            "github.com/levellerlabs/agent-renewal-guard",
        ], "github.com/levellerlabs/agent-renewal-guard - MIT")],
        "outro", ttsd["outro"][0], pad_tail=0.6))

    lst = WORK / "concat.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in segs))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-c", "copy", "-movflags", "+faststart",
                    str(OUT)], check=True)
    dur = float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(OUT)]).strip())
    print(f"DONE: {OUT} ({dur:.1f}s, {OUT.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
