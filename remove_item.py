#!/usr/bin/env python3
"""
INVENTORY ITEM REMOVAL TOOL
============================

Remove erroneous entries from inventory by tote ID and item sequence.
Automatically regenerates CSV after removal.

USAGE:
------
python remove_item.py TOTE-ID SEQUENCE

EXAMPLES:
---------
python remove_item.py TOTE-002 54      # Remove item #54 from TOTE-002
python remove_item.py TOTE-001 12      # Remove item #12 from TOTE-001

NOTE:
-----
Creates a backup before modification in the configured BACKUP_DIR.
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ========================================
# CONFIGURATION FROM .ENV
# ========================================

ORGANIZED_DIR = Path(os.getenv('ORGANIZED_DIR', '/home/user/organized'))
INVENTORY_JSON = Path(os.getenv('INVENTORY_JSON', str(ORGANIZED_DIR / 'inventory.json')))
INVENTORY_CSV = Path(os.getenv('INVENTORY_CSV', str(ORGANIZED_DIR / 'inventory.csv')))
BACKUP_DIR = Path(os.getenv('BACKUP_DIR', str(ORGANIZED_DIR / 'backups')))

def resequence_tote(inventory, tote_id):
    """Resequence items within a tote to remove gaps (1, 2, 3, ...)"""
    tote_items = [item for item in inventory if item.get('tote_id') == tote_id]
    tote_items.sort(key=lambda x: x.get('item_sequence', 0))
    for new_seq, item in enumerate(tote_items, start=1):
        item['item_sequence'] = new_seq

def regenerate_csv(inventory):
    """Regenerate CSV from inventory data"""
    with open(INVENTORY_CSV, 'w') as f:
        f.write("tote_id,item_sequence,item_name,category,estimated_value_usd,confidence,manual_review,status\n")
        for item in inventory:
            if item['status'] == 'success':
                ai = item['ai_analysis']
                review = "YES" if ai.get('manual_review_recommended') else "NO"
                f.write(f'"{item["tote_id"]}",{item.get("item_sequence",0)},"{item["item_name"]}","{ai.get("category","")}",{ai.get("estimated_value_usd",0)},"{ai.get("confidence","")}",{review},success\n')
            else:
                f.write(f'"{item.get("tote_id","")}",{item.get("item_sequence",0)},"","",0,"","",failed\n')

def remove_item(tote_id, sequence):
    """Remove item from inventory"""

    if not INVENTORY_JSON.exists():
        print(f"Inventory not found: {INVENTORY_JSON}")
        return False

    # Load inventory
    with open(INVENTORY_JSON, 'r') as f:
        inventory = json.load(f)

    # Find item to remove
    item_to_remove = None
    for item in inventory:
        if item.get('tote_id') == tote_id and item.get('item_sequence') == sequence:
            item_to_remove = item
            break

    if not item_to_remove:
        print(f"No item found: {tote_id} item #{sequence}")
        return False

    # Show what we're removing
    print(f"\nFound item to remove:")
    print(f"  Tote: {tote_id}")
    print(f"  Sequence: #{sequence}")
    if item_to_remove['status'] == 'success':
        print(f"  Name: {item_to_remove.get('item_name', 'Unknown')}")
        print(f"  Value: ${item_to_remove['ai_analysis'].get('estimated_value_usd', 0):.2f}")
    else:
        print(f"  Status: FAILED")

    # Confirm
    confirm = input(f"\nRemove this item? (y/n): ")
    if confirm.lower() != 'y':
        print("Cancelled.")
        return False

    # Create backup
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_file = BACKUP_DIR / f"inventory_before_removal_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(backup_file, 'w') as f:
        json.dump(inventory, f, indent=2)
    print(f"\nBackup created: {backup_file}")

    # Remove item
    inventory = [item for item in inventory
                 if not (item.get('tote_id') == tote_id and item.get('item_sequence') == sequence)]

    # Resequence remaining items in this tote to close gaps
    resequence_tote(inventory, tote_id)

    # Save updated inventory
    with open(INVENTORY_JSON, 'w') as f:
        json.dump(inventory, f, indent=2)

    # Regenerate CSV
    regenerate_csv(inventory)

    # Summary
    total_value = sum(item['ai_analysis']['estimated_value_usd']
                     for item in inventory if item['status'] == 'success')

    print(f"\nItem removed successfully")
    print(f"  Items remaining: {len(inventory)}")
    print(f"  Total collection value: ${total_value:,.2f}")
    print(f"  Updated: {INVENTORY_JSON}")
    print(f"  Updated: {INVENTORY_CSV}")

    return True

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("INVENTORY ITEM REMOVAL TOOL")
        print("="*50)
        print("\nUsage:")
        print("  python remove_item.py TOTE-ID SEQUENCE")
        print("\nExamples:")
        print("  python remove_item.py TOTE-002 54")
        print("  python remove_item.py TOTE-001 12")
        print("\nNote: Creates backup before removal")
        sys.exit(1)

    tote_id = sys.argv[1]
    try:
        sequence = int(sys.argv[2])
    except ValueError:
        print(f"Invalid sequence number: {sys.argv[2]}")
        sys.exit(1)

    success = remove_item(tote_id, sequence)
    sys.exit(0 if success else 1)
