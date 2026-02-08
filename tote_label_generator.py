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
  python generate_labels.py 50        # Generate 50 labels (TOTE-001 through TOTE-050)

Reprint single label:
  python generate_labels.py TOTE-023  # Regenerate specific label

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

import json
from pathlib import Path
import sys

def generate_zpl_label(tote_id):
    """Generate ZPL code for a single tote label (3x2 inch)"""
    
    # Simple QR data
    qr_data = json.dumps({
        "type": "INTL_MOVE_2026_TOTE",
        "tote_id": tote_id
    })
    
    # ZPL template for 3x2 label (203 DPI)
    zpl = f"""^XA
^FO30,30^BQN,2,5^FDMA,{qr_data}^FS
^FO280,60^A0N,70,70^FD{tote_id}^FS
^FO280,320^A0N,30,30^FDNew Country: New Home^FS
^XZ
"""
    return zpl

def generate_zpl_labels(num_totes, output_dir="zpl_labels"):
    """Generate ZPL files for sequential tote labels"""
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Generating ZPL labels for {num_totes} totes...\n")
    
    tote_ids = [f"TOTE-{i:03d}" for i in range(1, num_totes + 1)]
    
    # Generate individual ZPL files
    for idx, tote_id in enumerate(tote_ids, 1):
        zpl_code = generate_zpl_label(tote_id)
        
        zpl_file = output_path / f"{tote_id}.zpl"
        with open(zpl_file, 'w') as f:
            f.write(zpl_code)
        
        print(f"[{idx}/{num_totes}] {tote_id}")
    
    # Generate batch file for printing all labels
    batch_file = output_path / "print_all.zpl"
    with open(batch_file, 'w') as f:
        for tote_id in tote_ids:
            f.write(generate_zpl_label(tote_id))
    
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

def reprint_label(tote_id, output_dir="zpl_labels"):
    """Reprint a single label by tote ID"""
    
    output_path = Path(output_dir)
    zpl_file = output_path / f"{tote_id}.zpl"
    
    if not zpl_file.exists():
        # Regenerate if missing
        output_path.mkdir(parents=True, exist_ok=True)
        zpl_code = generate_zpl_label(tote_id)
        with open(zpl_file, 'w') as f:
            f.write(zpl_code)
        print(f"✓ Generated {zpl_file}")
    
    print(f"📄 Label file: {zpl_file}")
    print(f"\nTo print:")
    print(f"  cat {zpl_file} | lp -d YourZebraPrinter")
    
    return True

if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("Usage:")
        print("  Generate labels:    python generate_labels.py <number>")
        print("  Reprint label:      python generate_labels.py TOTE-001")
        print("\nExamples:")
        print("  python generate_labels.py 50          # Generate 50 labels")
        print("  python generate_labels.py TOTE-023    # Reprint single label")
        sys.exit(1)
    
    arg = sys.argv[1]
    
    if arg.startswith("TOTE-"):
        # Reprint single label
        reprint_label(arg)
    else:
        # Generate multiple labels
        num = int(arg)
        generate_zpl_labels(num)