#!/usr/bin/env python3
"""Batch harvest — imports all discovered folders smallest-to-largest.

Run:    python harvest.py
Resume: just run again; folders marked DONE are skipped automatically.
"""

import re
import subprocess
import pathlib
from datetime import datetime

LOG_FILE = pathlib.Path("harvest_progress.md")

# Folders sorted ascending by PDF count (Bahal_Shrivastava2/Papers already done)
FOLDERS = [
    (1,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Old teaching/Advanced Micro theory/Theory papers"),
    (1,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Old teaching/Topics in Micro Theory/Papers"),
    (1,  "/home/anand/cse Dropbox/Anand Shrivastava/University paper/CSIE - Pragati/Literature review/New papers"),
    (1,  "/home/anand/cse Dropbox/Anand Shrivastava/University paper/CSIE - Pragati/Literature review"),
    (2,  "/home/anand/cse Dropbox/Anand Shrivastava/Networks/Theory papers to read"),
    (3,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Honours/Revathy/Relevant Papers"),
    (3,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/1 Applied Game theory/1 Jan 2026/Papers"),
    (3,  "/home/anand/cse Dropbox/Anand Shrivastava/Bahal_Shrivastava2/References"),
    (4,  "/home/anand/cse Dropbox/Anand Shrivastava/When_links_matter/Papers"),
    (5,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Honours/1 ABM supply chain networks/References"),
    (5,  "/home/anand/cse Dropbox/Anand Shrivastava/Temple descration and riots/Literature/Assassinations and politics literature"),
    (5,  "/home/anand/cse Dropbox/Anand Shrivastava/IWS Network/Literature"),
    (6,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Microeconomics Theory and applications 1/Papers for presentation"),
    (6,  "/home/anand/cse Dropbox/Anand Shrivastava/University paper/CSIE - Pragati/Literature review/Literature Review"),
    (7,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Honours/Conflict/Riots - papers"),
    (7,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/1 Applied Game theory/Papers"),
    (7,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Old teaching/Quantitative methods/Readings"),
    (7,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Old teaching/Math models in Econ/Papers"),
    (7,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Old teaching/Econ History/Cool papers"),
    (8,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Old teaching/Econ History/Papers"),
    (8,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Old teaching/Econometrics/Papers"),
    (9,  "/home/anand/cse Dropbox/Anand Shrivastava/APU/Old teaching/Intermediate Micro/Papers"),
    (9,  "/home/anand/cse Dropbox/Anand Shrivastava/Published papers"),
    (11, "/home/anand/cse Dropbox/Anand Shrivastava/Temple descration and riots/Literature"),
    (12, "/home/anand/cse Dropbox/Anand Shrivastava/Discrimination paper/literature/preferences for job characteristics"),
    (13, "/home/anand/cse Dropbox/Anand Shrivastava/IWS Network/Publication/Nature Human Behavior/Literature"),
    (13, "/home/anand/cse Dropbox/Anand Shrivastava/University paper/CSIE - Pragati/Literature review/India papers"),
    (22, "/home/anand/cse Dropbox/Anand Shrivastava/Discrimination paper/literature"),
    (26, "/home/anand/cse Dropbox/Anand Shrivastava/Experienced Inequality/Literature"),
    (34, "/home/anand/cse Dropbox/Anand Shrivastava/IWS Network/Draft/Papers"),
    (36, "/home/anand/cse Dropbox/Anand Shrivastava/Temple descration and riots/January 2020/Literature"),
    (38, "/home/anand/cse Dropbox/Anand Shrivastava/IWS Network/Zaeen/Papers"),
]


def load_done_folders():
    if not LOG_FILE.exists():
        return set()
    done = set()
    for line in LOG_FILE.read_text().splitlines():
        m = re.match(r"### DONE — (.+)", line)
        if m:
            done.add(m.group(1).strip())
    return done


def run_import(folder_path):
    result = subprocess.run(
        ["python", "main.py", "--verbose", "import",
         f"--folders={folder_path}", "--all", "--ai-verify"],
        input="\n",
        capture_output=True,
        text=True,
        cwd=str(pathlib.Path(__file__).parent),
    )
    return result.stdout + result.stderr


def parse_output(output):
    imported  = int(m.group(1)) if (m := re.search(r"imported\s+(\d+)",   output)) else 0
    ai_rej    = int(m.group(1)) if (m := re.search(r"ai_rejected\s+(\d+)", output)) else 0
    failed    = int(m.group(1)) if (m := re.search(r"failed\s+(\d+)",      output)) else 0
    no_cands  = "No untracked PDF candidates found" in output

    rejected_items = re.findall(r"AI rejected: (.+?)\s{2,}title=(.+?)(?:\n|$)", output)
    low_conf       = re.findall(r'\[conf: (7\.0|12\.0)\] "([^"]+)"', output)

    return {
        "imported": imported,
        "ai_rejected": ai_rej,
        "failed": failed,
        "no_candidates": no_cands,
        "rejected_items": rejected_items,
        "low_conf_imports": low_conf,
    }


def append_to_log(folder_path, pdf_count, results, timestamp):
    lines = [
        f"\n### DONE — {folder_path}",
        f"*{timestamp} | {pdf_count} PDFs in folder*",
        f"- Imported: **{results['imported']}**  |  "
        f"AI-rejected: {results['ai_rejected']}  |  "
        f"Failed: {results['failed']}",
    ]
    if results["no_candidates"]:
        lines.append("- *(all PDFs already in Zotero)*")

    if results["rejected_items"]:
        lines.append("\n**AI-rejected (add to Zotero manually if wanted):**")
        for fname, title in results["rejected_items"]:
            lines.append(f"- `{fname.strip()}` — *{title.strip()}*")

    if results["low_conf_imports"]:
        lines.append("\n**Low-confidence imports (verify metadata in Zotero):**")
        for conf, title in results["low_conf_imports"]:
            lines.append(f"- conf {conf}: *{title.strip()}*")

    lines.append("")
    with open(LOG_FILE, "a") as f:
        f.write("\n".join(lines) + "\n")


def main():
    done = load_done_folders()
    total = len(FOLDERS)
    newly_done = 0

    print(f"Harvest starting — {total} folders to process, {len(done)} already done.\n")

    for i, (pdf_count, folder_path) in enumerate(FOLDERS, 1):
        folder_name = pathlib.Path(folder_path).name
        if folder_path in done:
            print(f"[{i:>2}/{total}] SKIP  {folder_name}")
            continue

        print(f"\n[{i:>2}/{total}] ── {pdf_count} PDFs ── {folder_path}")
        output = run_import(folder_path)
        results = parse_output(output)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        append_to_log(folder_path, pdf_count, results, timestamp)
        newly_done += 1

        status = (f"imported {results['imported']}"
                  + (f"  ai_rejected {results['ai_rejected']}" if results["ai_rejected"] else "")
                  + (f"  FAILED {results['failed']}" if results["failed"] else "")
                  + ("  (all already tracked)" if results["no_candidates"] else ""))
        print(f"         → {status}")

    print(f"\n{'='*60}")
    if newly_done:
        print(f"Done. {newly_done} folder(s) processed this run.")
        print(f"Review {LOG_FILE} for cleanup items.")
    else:
        print("All folders already done.")


if __name__ == "__main__":
    main()
