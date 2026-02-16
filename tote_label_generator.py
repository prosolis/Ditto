#!/usr/bin/env python3
"""
ZEBRA LABEL GENERATOR FOR TOTE IDs
===================================

Generate ZPL labels with QR codes for tote identification.
Simple sequential numbering: TOTE-001, TOTE-002, etc.

SETUP:
------
No setup required - just run the script!

USAGE:
------
Generate labels:
  python generate_labels.py 50                            # Generate 50 labels (TOTE-001 through TOTE-050)
  python generate_labels.py 50 --label "MOVE TO PARIS"    # Custom label info text

Reprint single label:
  python generate_labels.py TOTE-023                      # Regenerate specific label
  python generate_labels.py TOTE-023 --label "FRAGILE"    # Reprint with custom text

PRINTING:
---------
Linux:
  cat zpl_labels/print_all.zpl | lp -d YourZebraPrinter
  cat zpl_labels/TOTE-001.zpl | lp -d YourZebraPrinter

Windows:
  Copy .zpl files to printer via USB or network

LABEL SPECS:
------------
- Size: 3x2 inches
- Resolution: 203 DPI
- QR Code: Error correction level H (highest)
- Format: Simple sequential numbering

OUTPUT:
-------
- Individual .zpl files for each label
- print_all.zpl for batch printing
- seal_tracking.json template for associating physical seals
"""

import argparse
import json
from pathlib import Path
import sys

DEFAULT_LABEL_INFO = "INTL MOVE 2026"
MAX_CHARS_PER_LINE = 20
MAX_LINES = 2


def wrap_label_text(text, max_chars=MAX_CHARS_PER_LINE, max_lines=MAX_LINES):
    """Wrap label text into lines that fit the 3x2 label.

    Splits on word boundaries when possible. Truncates with '...' if the
    text cannot fit within the allowed space.
    """
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    lines = []
    remaining = text
    for i in range(max_lines):
        if len(remaining) <= max_chars:
            lines.append(remaining)
            remaining = ""
            break
        # Find the last space within the limit to break on a word boundary
        split_at = remaining[:max_chars].rfind(" ")
        if split_at <= 0:
            # No space found — hard break
            split_at = max_chars
        chunk = remaining[:split_at].rstrip()
        remaining = remaining[split_at:].lstrip()
        if i == max_lines - 1 and remaining:
            # Last allowed line but text remains — truncate
            chunk = chunk[:max_chars - 3] + "..."
        lines.append(chunk)

    return lines


def generate_zpl_label(tote_id, label_info=DEFAULT_LABEL_INFO):
    """Generate ZPL code for a single tote label (3x2 inch)"""

    # Simple QR data
    qr_data = json.dumps({
        "type": "INTL_MOVE_2026_TOTE",
        "tote_id": tote_id
    })

    # Build label info field (possibly multi-line)
    info_lines = wrap_label_text(label_info)
    info_zpl = ""
    y_pos = 320
    for line in info_lines:
        info_zpl += f"^FO280,{y_pos}^A0N,30,30^FD{line}^FS\n"
        y_pos += 35

    # ZPL template for 3x2 label (203 DPI)
    zpl = f"""^XA
^FO30,30^BQN,2,5^FDMA,{qr_data}^FS
^FO280,60^A0N,70,70^FD{tote_id}^FS
{info_zpl}^XZ
"""
    return zpl

def generate_zpl_labels(num_totes, output_dir="zpl_labels", label_info=DEFAULT_LABEL_INFO):
    """Generate ZPL files for sequential tote labels"""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print(f"Generating ZPL labels for {num_totes} totes...")
    print(f"  Label info: {label_info}\n")

    tote_ids = [f"TOTE-{i:03d}" for i in range(1, num_totes + 1)]

    # Generate individual ZPL files
    for idx, tote_id in enumerate(tote_ids, 1):
        zpl_code = generate_zpl_label(tote_id, label_info)

        zpl_file = output_path / f"{tote_id}.zpl"
        with open(zpl_file, 'w') as f:
            f.write(zpl_code)

        print(f"[{idx}/{num_totes}] {tote_id}")

    # Generate batch file for printing all labels
    batch_file = output_path / "print_all.zpl"
    with open(batch_file, 'w') as f:
        for tote_id in tote_ids:
            f.write(generate_zpl_label(tote_id, label_info))
    
    # Generate seal tracking template
    seal_tracking = output_path / "seal_tracking.json"
    seal_template = {tote_id: "" for tote_id in tote_ids}
    with open(seal_tracking, 'w') as f:
        json.dump(seal_template, f, indent=2)
    
    print(f"\n✓ Complete!")
    print(f"  ZPL files: {output_path}/")
    print(f"  Seal tracking: {seal_tracking}")
    print(f"\nPrinting:")
    print(f"  All labels: cat {batch_file} | lp -d YourZebraPrinter")
    print(f"  Single label: cat {output_path}/TOTE-001.zpl | lp -d YourZebraPrinter")
    
    return tote_ids

def reprint_label(tote_id, output_dir="zpl_labels", label_info=DEFAULT_LABEL_INFO):
    """Reprint a single label by tote ID"""

    output_path = Path(output_dir)
    zpl_file = output_path / f"{tote_id}.zpl"

    # Always regenerate so label_info overrides take effect
    output_path.mkdir(parents=True, exist_ok=True)
    zpl_code = generate_zpl_label(tote_id, label_info)
    with open(zpl_file, 'w') as f:
        f.write(zpl_code)
    print(f"✓ Generated {zpl_file}")
    
    print(f"📄 Label file: {zpl_file}")
    print(f"\nTo print:")
    print(f"  cat {zpl_file} | lp -d YourZebraPrinter")
    
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate ZPL labels with QR codes for tote identification."
    )
    parser.add_argument(
        "target",
        help="Number of labels to generate (e.g. 50) or a single tote ID to reprint (e.g. TOTE-023)"
    )
    parser.add_argument(
        "-l", "--label",
        default=DEFAULT_LABEL_INFO,
        help=f"Override the label info text (default: '{DEFAULT_LABEL_INFO}'). "
             f"Max {MAX_CHARS_PER_LINE} chars/line, wraps to {MAX_LINES} lines."
    )
    args = parser.parse_args()

    if args.target.startswith("TOTE-"):
        reprint_label(args.target, label_info=args.label)
    else:
        num = int(args.target)
        generate_zpl_labels(num, label_info=args.label)