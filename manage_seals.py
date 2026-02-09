#!/usr/bin/env python3
"""
SECURITY SEAL MANAGEMENT TOOL
===============================

Manage security seal to tote ID associations. Assigns physical numbered
security seals to totes for tracking during international shipment.

SETUP:
------
Run generate_labels.py first to create tote labels and the seal_tracking.json
template file.

USAGE:
------
View assignments:
  python manage_seals.py view

Assign single seal:
  python manage_seals.py assign TOTE-001 AB123456

Bulk assignment (interactive):
  python manage_seals.py bulk

EXAMPLES:
---------
python manage_seals.py view                      # Show all seal assignments
python manage_seals.py assign TOTE-001 AB123456  # Assign seal to tote
python manage_seals.py bulk                      # Interactive bulk mode

NOTE:
-----
Seal tracking data is stored in zpl_labels/seal_tracking.json.
"""

import json
from pathlib import Path
import sys

SEAL_TRACKING_FILE = Path("zpl_labels/seal_tracking.json")

def load_seal_tracking():
    """Load existing seal tracking data"""
    if not SEAL_TRACKING_FILE.exists():
        print(f"Seal tracking file not found: {SEAL_TRACKING_FILE}")
        print("   Run generate_labels.py first to create it.")
        sys.exit(1)

    with open(SEAL_TRACKING_FILE, 'r') as f:
        return json.load(f)

def save_seal_tracking(data):
    """Save seal tracking data"""
    with open(SEAL_TRACKING_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def assign_seal(tote_id, seal_number):
    """Assign a seal number to a tote"""
    tracking = load_seal_tracking()

    if tote_id not in tracking:
        print(f"Tote ID '{tote_id}' not found.")
        print(f"   Available totes: {', '.join(sorted(tracking.keys()))}")
        return False

    # Check if seal already used
    for existing_tote, existing_seal in tracking.items():
        if existing_seal == seal_number and existing_seal != "":
            print(f"Seal {seal_number} already assigned to {existing_tote}")
            confirm = input(f"   Reassign to {tote_id}? (y/n): ")
            if confirm.lower() != 'y':
                print("   Cancelled.")
                return False
            tracking[existing_tote] = ""  # Clear old assignment

    tracking[tote_id] = seal_number
    save_seal_tracking(tracking)

    print(f"Assigned seal {seal_number} to {tote_id}")
    return True

def view_seals():
    """View all seal assignments"""
    tracking = load_seal_tracking()

    assigned = [(k, v) for k, v in tracking.items() if v]
    unassigned = [k for k, v in tracking.items() if not v]

    print("\n" + "="*60)
    print("SEAL ASSIGNMENTS")
    print("="*60)

    if assigned:
        print(f"\nAssigned ({len(assigned)}):")
        for tote_id, seal_number in sorted(assigned):
            print(f"  {tote_id} -> {seal_number}")

    if unassigned:
        print(f"\nUnassigned ({len(unassigned)}):")
        for tote_id in sorted(unassigned):
            print(f"  {tote_id}")

    print(f"\n{'='*60}\n")

def bulk_assign():
    """Interactive bulk seal assignment"""
    tracking = load_seal_tracking()
    unassigned = sorted([k for k, v in tracking.items() if not v])

    if not unassigned:
        print("All totes have seals assigned!")
        return

    print(f"\n{len(unassigned)} totes need seal assignments")
    print("Enter seal numbers (or 'q' to quit, 's' to skip):\n")

    for tote_id in unassigned:
        while True:
            seal_number = input(f"{tote_id}: ").strip()

            if seal_number.lower() == 'q':
                print("Saving and exiting...")
                save_seal_tracking(tracking)
                return

            if seal_number.lower() == 's' or seal_number == '':
                print("  Skipped.")
                break

            # Check if seal already used
            already_used = None
            for existing_tote, existing_seal in tracking.items():
                if existing_seal == seal_number:
                    already_used = existing_tote
                    break

            if already_used:
                print(f"  Already assigned to {already_used}")
                continue

            tracking[tote_id] = seal_number
            print(f"  Assigned")
            break

    save_seal_tracking(tracking)
    print("\nAll assignments saved!")

if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("SECURITY SEAL MANAGEMENT TOOL")
        print("="*50)
        print("\nUsage:")
        print("  View assignments:        python manage_seals.py view")
        print("  Assign single seal:      python manage_seals.py assign TOTE-001 AB123456")
        print("  Bulk assignment:         python manage_seals.py bulk")
        print("\nExamples:")
        print("  python manage_seals.py view")
        print("  python manage_seals.py assign TOTE-001 AB123456")
        print("  python manage_seals.py bulk    # Interactive mode")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "view":
        view_seals()

    elif command == "assign":
        if len(sys.argv) != 4:
            print("Usage: python manage_seals.py assign TOTE-ID SEAL-NUMBER")
            print("Example: python manage_seals.py assign TOTE-001 AB123456")
            sys.exit(1)
        assign_seal(sys.argv[2], sys.argv[3])

    elif command == "bulk":
        bulk_assign()

    else:
        print(f"Unknown command: {command}")
        print("Valid commands: view, assign, bulk")
        sys.exit(1)
