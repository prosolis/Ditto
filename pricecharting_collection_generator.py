#!/usr/bin/env python3
"""
PRICECHARTING COLLECTION GENERATOR
===================================

Generates PriceCharting-compatible text files for their collection bulk upload
feature. Reads inventory.json and produces one text file per supported category:

  - videogames.txt  (Video Game Software, Console, Accessory, Handheld)
  - cards.txt       (Trading Cards)
  - comics.txt      (Comic Books)
  - legos.txt       (LEGO)

Each line contains a quoted search string that PriceCharting can match
against their product database.

SETUP:
------
Uses same .env configuration as other Ditto scripts.

USAGE:
------
# Generate from default inventory location
python pricecharting_collection_generator.py

# Specify inventory file
python pricecharting_collection_generator.py --inventory /path/to/inventory.json

# Specify output directory
python pricecharting_collection_generator.py --output-dir /path/to/output

FORMAT EXAMPLES:
----------------
Video Games: "Call of Duty Black Ops PS3"
             "Mario 2 NES Sealed"
             "Donkey Kong 3 PAL NES"
Trading Cards: "Charizard Pokemon Base Set #4"
Comics:        "Action Comics #13 1939 CGC 8"
LEGO:          "Fire Mario #71370 LEGO Super Mario"
"""

import json
import os
import re
import argparse
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ========================================
# CONFIGURATION FROM .ENV
# ========================================

ORGANIZED_DIR = Path(os.getenv('ORGANIZED_DIR', '/home/user/organized'))
INVENTORY_FILE = Path(os.getenv('INVENTORY_JSON', str(ORGANIZED_DIR / 'inventory.json')))

# Category groupings for output files
VIDEO_GAME_CATEGORIES = {
    'Video Game Software',
    'Video Game Console',
    'Video Game Accessory',
    'Handheld Game System'
}
LEGO_CATEGORIES = {'LEGO'}
COMIC_CATEGORIES = {'Comic Books'}
CARD_CATEGORIES = {'Trading Cards'}

# ========================================
# FORMAT FUNCTIONS
# ========================================

CONDITION_MAP = {
    'LOOSE_CART': 'Item Only',
    'COMPLETE_IN_BOX': 'CIB',
    'NEW_SEALED': 'Sealed',
}


def format_video_game(item):
    """
    Format: {name} [PAL] {platform} [condition]

    Examples:
        Call of Duty Black Ops PS3 Item Only
        Mario 2 NES Sealed
        Donkey Kong 3 PAL NES CIB
    """
    ai = item['ai_analysis']
    parts = [ai['item_name']]

    if ai.get('region') == 'PAL':
        parts.append('PAL')

    if ai.get('platform'):
        parts.append(ai['platform'])

    condition = CONDITION_MAP.get(ai.get('pricing_basis', ''))
    if condition:
        parts.append(condition)

    return ' '.join(parts)


def format_trading_card(item):
    """
    Format: {card_name} [#{number}] [{grader} {grade}]

    Example:
        Charizard Pokemon Base Set #4

    Note: item_name is expected to include set name
    (e.g., "Charizard Pokemon Base Set")
    """
    ai = item['ai_analysis']
    name = ai['item_name']
    parts = [name]

    issue = ai.get('issue_number')
    if issue and f'#{issue}' not in name:
        parts.append(f'#{issue}')

    grader = ai.get('grader')
    grade = ai.get('grade')
    if grader and grade is not None:
        parts.append(f'{grader} {grade}')
    elif grade is not None:
        parts.append(str(grade))

    return ' '.join(parts)


def format_comic(item):
    """
    Format: {title} [#{issue}] [{year}] [{grader} {grade}]

    Example:
        Action Comics #13 1939 CGC 8
    """
    ai = item['ai_analysis']
    name = ai['item_name']
    parts = [name]

    issue = ai.get('issue_number')
    if issue and f'#{issue}' not in name:
        parts.append(f'#{issue}')

    year = ai.get('year')
    if year and str(year) not in name:
        parts.append(str(year))

    grader = ai.get('grader')
    grade = ai.get('grade')
    if grader and grade is not None:
        parts.append(f'{grader} {grade}')
    elif grade is not None:
        parts.append(str(grade))

    return ' '.join(parts)


def format_lego(item):
    """
    Format: {name} [#{set_number}] LEGO [{theme}]

    Example:
        Fire Mario #71370 LEGO Super Mario

    Uses PriceCharting data when available for accurate product name and theme.
    """
    ai = item['ai_analysis']
    pc_data = item.get('pricecharting_data')

    if pc_data and len(pc_data) > 0:
        # Use the matched PriceCharting entry
        match_idx = ai.get('pricecharting_match_used')
        if match_idx and isinstance(match_idx, int) and 1 <= match_idx <= len(pc_data):
            pc = pc_data[match_idx - 1]
        else:
            pc = pc_data[0]

        product_name = pc.get('product_name') or ai['item_name']
        category = pc.get('category', '')

        # Normalize bracket notation: "[#71370]" -> "#71370"
        product_name = re.sub(r'\[#(\d+)\]', r'#\1', product_name)

        # Extract theme from PriceCharting category
        # e.g., "LEGO Super Mario" -> "Super Mario"
        theme = ''
        if category:
            if category.upper().startswith('LEGO '):
                theme = category[5:]
            elif category.upper() != 'LEGO':
                theme = category

        parts = [product_name]
        if 'LEGO' not in product_name.upper():
            parts.append('LEGO')
        if theme and theme.lower() not in product_name.lower():
            parts.append(theme)

        return ' '.join(parts)

    # Fallback: item name + LEGO
    name = ai['item_name']
    parts = [name]
    if 'LEGO' not in name.upper():
        parts.append('LEGO')
    return ' '.join(parts)


# ========================================
# CATEGORIZATION
# ========================================

FORMAT_FUNCTIONS = {
    'videogames': format_video_game,
    'legos': format_lego,
    'comics': format_comic,
    'cards': format_trading_card,
}


def categorize_item(item):
    """Determine which output file an item belongs to. Returns category key or None."""
    if item.get('status') != 'success':
        return None

    ai = item.get('ai_analysis')
    if not ai:
        return None

    category = ai.get('category', '')

    if category in VIDEO_GAME_CATEGORIES:
        return 'videogames'
    elif category in LEGO_CATEGORIES:
        return 'legos'
    elif category in COMIC_CATEGORIES:
        return 'comics'
    elif category in CARD_CATEGORIES:
        return 'cards'

    return None


# ========================================
# MAIN LOGIC
# ========================================

def generate_collection_files(inventory_path, output_dir):
    """Read inventory and generate PriceCharting collection files."""

    if not inventory_path.exists():
        print(f"Error: Inventory file not found: {inventory_path}")
        return False

    with open(inventory_path, 'r') as f:
        inventory = json.load(f)

    # Categorize all items
    categorized = {
        'videogames': [],
        'legos': [],
        'comics': [],
        'cards': [],
    }

    skipped_failed = 0
    skipped_other = 0

    for item in inventory:
        cat = categorize_item(item)
        if cat:
            categorized[cat].append(item)
        elif item.get('status') == 'failed':
            skipped_failed += 1
        else:
            skipped_other += 1

    # Generate output files
    output_dir.mkdir(parents=True, exist_ok=True)

    files_written = []
    format_errors = 0

    for cat_key, items in categorized.items():
        if not items:
            continue

        format_fn = FORMAT_FUNCTIONS[cat_key]
        output_file = output_dir / f'{cat_key}.txt'

        lines = []
        for item in items:
            try:
                formatted = format_fn(item)
                lines.append(f'"{formatted}"')
            except Exception as e:
                tote = item.get('tote_id', '?')
                name = item.get('item_name', '?')
                print(f"  Warning: Could not format {name} ({tote}): {e}")
                format_errors += 1

        if lines:
            with open(output_file, 'w') as f:
                f.write('\n'.join(lines) + '\n')
            files_written.append((cat_key, output_file, len(lines)))

    # Print summary
    print(f"\n{'='*60}")
    print("PRICECHARTING COLLECTION FILES GENERATED")
    print(f"{'='*60}")
    print(f"Source: {inventory_path}")
    print(f"Total items in inventory: {len(inventory)}")
    print()

    if files_written:
        print("Files generated:")
        for cat_key, path, count in files_written:
            print(f"  {path} ({count} items)")
    else:
        print("No items found for any PriceCharting category.")

    if skipped_failed:
        print(f"\nSkipped {skipped_failed} failed items")
    if skipped_other:
        print(f"Skipped {skipped_other} items in non-PriceCharting categories")
    if format_errors:
        print(f"Format errors: {format_errors}")

    total_items = sum(count for _, _, count in files_written)
    print(f"\nTotal items for PriceCharting: {total_items}")
    print(f"{'='*60}\n")

    return True


def main():
    parser = argparse.ArgumentParser(
        description='Generate PriceCharting collection upload files from inventory'
    )
    parser.add_argument(
        '--inventory',
        type=Path,
        default=INVENTORY_FILE,
        help=f'Path to inventory.json (default: {INVENTORY_FILE})'
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=None,
        help='Output directory for collection files (default: <inventory_dir>/pricecharting)'
    )

    args = parser.parse_args()

    output_dir = args.output_dir or args.inventory.parent / 'pricecharting'

    print("=" * 60)
    print("PRICECHARTING COLLECTION GENERATOR")
    print("=" * 60)

    generate_collection_files(args.inventory, output_dir)


if __name__ == "__main__":
    main()
