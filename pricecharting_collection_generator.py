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
# PLATFORM NORMALIZATION
# ========================================

# Maps verbose platform names to preferred abbreviated forms
PLATFORM_NORMALIZE = {
    # Nintendo
    'Nintendo Entertainment System': 'NES',
    'Nintendo Entertainment System (NES)': 'NES',
    'Super Nintendo Entertainment System': 'SNES',
    'Super Nintendo Entertainment System (SNES)': 'SNES',
    'Super Nintendo': 'SNES',
    'Nintendo 64': 'N64',
    'Nintendo GameCube': 'GameCube',
    'Nintendo Wii': 'Wii',
    'Nintendo Wii U': 'Wii U',
    'Nintendo Switch': 'Switch',
    'Game Boy Advance': 'GBA',
    'Game Boy Color': 'GBC',
    'Nintendo Game Boy': 'GB',
    'Game Boy': 'GB',
    'Nintendo DS': 'NDS',
    'Nintendo 3DS': '3DS',
    # PlayStation
    'PlayStation': 'PS1',
    'Sony PlayStation': 'PS1',
    'PlayStation 1': 'PS1',
    'PlayStation 2': 'PS2',
    'PlayStation 3': 'PS3',
    'PlayStation 4': 'PS4',
    'PlayStation 5': 'PS5',
    'PlayStation Portable': 'PSP',
    'PlayStation Portable (PSP)': 'PSP',
    'Sony PlayStation Portable': 'PSP',
    'PlayStation Vita': 'Vita',
    'PS Vita': 'Vita',
    # Xbox
    'Microsoft Xbox': 'Xbox',
    'Xbox Series X|S': 'Xbox Series X',
    # Sega
    'Sega Genesis': 'Genesis',
    'Sega Saturn': 'Saturn',
    'Sega Dreamcast': 'Dreamcast',
    'Sega Master System': 'SMS',
    'Master System': 'SMS',
    'Sega 32X': '32X',
    # Regional
    'Super Famicom': 'SFC',
    'Famicom': 'FC',
    'PC Engine': 'PCE',
    'Mega Drive': 'MD',
    'TurboGrafx-16': 'TG-16',
    # Alternate abbreviations
    'PSX': 'PS1',
    'NDS': 'NDS',
    'DS': 'NDS',
}

# Japanese platform abbreviations paired with their US equivalents.
# PriceCharting lists these as separate categories.
REGIONAL_PLATFORM_PAIRS = {
    'SFC': 'SNES',
    'FC': 'NES',
    'PCE': 'TG-16',
    'MD': 'Genesis',
}
_US_TO_JP_PLATFORM = {v: k for k, v in REGIONAL_PLATFORM_PAIRS.items()}

# All known platform strings for stripping from item names, sorted longest first
_ALL_PLATFORM_NAMES = sorted(
    set(list(PLATFORM_NORMALIZE.keys()) + list(PLATFORM_NORMALIZE.values()) + [
        # Verbose forms that LLM might embed in item names
        'Super Famicom', 'Famicom', 'Mega Drive', 'PC Engine',
        'TurboGrafx-16', 'Atari 2600', 'Atari 7800',
        'Game Boy', 'GB', 'GBC', 'GBA',
        'NES', 'SNES', 'N64', 'NDS',
        'PS1', 'PS2', 'PS3', 'PS4', 'PS5', 'PSP', 'PS Vita', 'Vita',
        'GameCube', 'Wii', 'Wii U', 'Switch',
        'Xbox', 'Xbox 360', 'Xbox One', 'Xbox Series X',
        'Genesis', 'Saturn', 'Dreamcast', 'SMS', 'Master System',
        'Sega CD', '32X', 'DS', '3DS',
        # Abbreviated regional forms
        'SFC', 'FC', 'PCE', 'MD', 'TG-16',
    ]),
    key=len, reverse=True
)


def normalize_platform(platform, region=None):
    """Normalize platform name to preferred PriceCharting short form.

    Handles compound platform strings like 'Super Famicom SNES' by
    extracting individual platform names and using region to pick the
    correct one (NTSC-J -> Japanese name, NTSC-U -> US name).
    """
    if not platform:
        return platform

    # Direct lookup
    if platform in PLATFORM_NORMALIZE:
        result = PLATFORM_NORMALIZE[platform]
        if region == 'NTSC-J' and result in _US_TO_JP_PLATFORM:
            return _US_TO_JP_PLATFORM[result]
        return result

    # Compound platform string — extract recognized names (longest first)
    found = []
    remaining = platform
    for name in _ALL_PLATFORM_NAMES:
        if re.search(r'\b' + re.escape(name) + r'\b', remaining, re.IGNORECASE):
            normalized = PLATFORM_NORMALIZE.get(name, name)
            if normalized not in found:
                found.append(normalized)
            # Remove matched portion to prevent sub-matches (e.g. "Famicom" inside "Super Famicom")
            remaining = re.sub(r'\b' + re.escape(name) + r'\b', '', remaining, flags=re.IGNORECASE).strip()

    if not found:
        return platform

    if len(found) == 1:
        result = found[0]
        if region == 'NTSC-J' and result in _US_TO_JP_PLATFORM:
            return _US_TO_JP_PLATFORM[result]
        return result

    # Multiple platforms found — pick based on region
    if region == 'NTSC-J':
        for f in found:
            if f in REGIONAL_PLATFORM_PAIRS:
                return f
    elif region == 'NTSC-U':
        for f in found:
            if f not in REGIONAL_PLATFORM_PAIRS:
                return f

    return found[0]


def strip_platform_from_name(name, platform=None):
    """Remove platform references from item name to avoid redundancy.

    Strips parenthetical forms like '(NES)' anywhere, and bare platform
    names from the end of the string. Only removes trailing matches to
    avoid mangling game names like 'Wii Sports'.
    """
    if not name:
        return name

    # Build the list of strings to strip, including the specific platform
    to_strip = list(_ALL_PLATFORM_NAMES)
    if platform:
        to_strip.extend([platform, PLATFORM_NORMALIZE.get(platform, platform)])
        to_strip = sorted(set(to_strip), key=len, reverse=True)

    # Remove parenthetical platform references anywhere: "(NES)", "(PSP)", etc.
    for p in to_strip:
        name = re.sub(r'\s*\(' + re.escape(p) + r'\)', '', name, flags=re.IGNORECASE)

    # Repeatedly strip platform names from the end of the string
    changed = True
    while changed:
        changed = False
        for p in to_strip:
            pattern = re.compile(r'\s+' + re.escape(p) + r'\s*$', re.IGNORECASE)
            new_name = pattern.sub('', name)
            if new_name != name:
                name = new_name
                changed = True
                break

    return name.strip()


# ========================================
# FORMAT FUNCTIONS
# ========================================

CONDITION_MAP = {
    'COMPLETE_IN_BOX': 'CIB',
    'NEW_SEALED': 'Sealed',
}


def format_video_game(item):
    """
    Format: {name} {platform} [condition]

    Condition is only appended for CIB or Sealed. Loose (Item Only) is
    implied by omission on PriceCharting.

    Platform names embedded in the item name by the LLM are stripped and
    replaced with the normalized short form from the platform field.

    Examples:
        Call of Duty Black Ops PS3
        Mario 2 NES Sealed
        Donkey Kong 3 NES CIB
    """
    ai = item['ai_analysis']
    platform = normalize_platform(ai.get('platform'), ai.get('region'))
    name = strip_platform_from_name(ai['item_name'], ai.get('platform'))

    parts = [name]

    if platform:
        parts.append(platform)

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

def generate_collection_files(inventory_path, output_dir, tote_filter=None):
    """Read inventory and generate PriceCharting collection files.

    Args:
        inventory_path: Path to inventory.json
        output_dir: Directory for output files
        tote_filter: If set, only include items from this tote (e.g. 'TOTE-003')
    """

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
        if tote_filter and item.get('tote_id') != tote_filter:
            continue
        cat = categorize_item(item)
        if cat:
            categorized[cat].append(item)
        elif item.get('status') == 'failed':
            skipped_failed += 1
        else:
            skipped_other += 1

    # Build filename suffix for tote-specific output
    tote_suffix = f'-{tote_filter.lower()}' if tote_filter else ''

    # Generate output files
    output_dir.mkdir(parents=True, exist_ok=True)

    files_written = []
    format_errors = 0

    for cat_key, items in categorized.items():
        if not items:
            continue

        format_fn = FORMAT_FUNCTIONS[cat_key]
        output_file = output_dir / f'{cat_key}{tote_suffix}.txt'

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
    if tote_filter:
        print(f"Tote filter: {tote_filter}")
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
    parser.add_argument(
        '--tote',
        type=str,
        default=None,
        help='Only generate files for a specific tote (e.g. TOTE-003)'
    )

    args = parser.parse_args()

    output_dir = args.output_dir or args.inventory.parent / 'pricecharting'

    print("=" * 60)
    print("PRICECHARTING COLLECTION GENERATOR")
    print("=" * 60)

    generate_collection_files(args.inventory, output_dir, tote_filter=args.tote)


if __name__ == "__main__":
    main()
