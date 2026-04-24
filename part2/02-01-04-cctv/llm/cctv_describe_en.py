#!/usr/bin/env python3
# llava_cctv_describe_en.py
# Usage: python3 llava_cctv_describe_en.py img1.jpg img2.jpg img3.jpg img4.jpg img5.jpg

import sys, base64, requests
from pathlib import Path

HOST = "http://localhost:11434"
MODEL = "llava:7b"

PROMPT_EN = (
    "You are an expert traffic and pedestrian analyst. "
    "Given the following five sequential CCTV snapshots, write ONE concise paragraph in English. "
    "Cover exactly these aspects: (1) weather, (2) pedestrian congestion, (3) road traffic volume, "
    "(4) brief visual evidence you relied on (e.g., shadows, umbrellas, wet roads, density, speed cues). "
    "State only observable facts. No lists, no JSON, no headings—just one paragraph."
)

def to_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")

def main():
    if len(sys.argv) != 6:
        print("Error: provide exactly 5 image paths.", file=sys.stderr)
        print("Example: python3 llava_cctv_describe_en.py 1.jpg 2.jpg 3.jpg 4.jpg 5.jpg", file=sys.stderr)
        sys.exit(2)

    imgs = [Path(p) for p in sys.argv[1:]]
    for p in imgs:
        if not p.exists():
            print(f"File not found: {p}", file=sys.stderr)
            sys.exit(2)

    payload = {
        "model": MODEL,
        "prompt": PROMPT_EN,
        "images": [to_b64(p) for p in imgs],  # pure base64, no data URL prefix
        "stream": False,
        "options": {"temperature": 0.2}
    }

    r = requests.post(f"{HOST}/api/generate", json=payload, timeout=180)
    r.raise_for_status()
    data = r.json()
    print(data.get("response", "").strip())

if __name__ == "__main__":
    main()
