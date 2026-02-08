#!/usr/bin/env python3
"""
PRICECHARTING PRICE UPDATER
============================

Batch process to add/update PriceCharting pricing data in existing inventory.
Run this separately after initial scanning, or periodically to refresh prices.

SETUP:
------
1. Add to .env:
   PRICECHARTING_API_KEY=your_key_here

2. Complete initial inventory scanning first

USAGE:
------
# Update all eligible items
python update_pricecharting.py

# Only update items without PriceCharting data
python update_pricecharting.py --new-only

# Only update specific categories
python update_pricecharting.py --categories "Video Game Software" "LEGO" "Comic Books"

# Dry run (show what would be updated)
python update_pricecharting.py --dry-run

WHAT IT DOES:
-------------
1. Reads inventory.json
2. Identifies items eligible for PriceCharting (games, LEGO, comics)
3. Queries PriceCharting API for current pricing
4. Updates inventory with PriceCharting data
5. Recalculates estimated values based on best available data
6. Saves updated inventory.json and inventory.csv

USE CASES:
----------
- Initial run after scanning to add PriceCharting data
- Annual refresh for insurance documentation
- Pre-move update to get current market values
- Spot-check specific items that were flagged for manual review
"""

import requests
import json
import os
import re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import argparse

# Load environment variables
load_dotenv()

# ========================================
# CONFIGURATION FROM .ENV
# ========================================

PRICECHARTING_API_KEY = os.getenv('PRICECHARTING_API_KEY')
PRICECHARTING_MAX_RESULTS = int(os.getenv('PRICECHARTING_MAX_RESULTS', '5'))

ORGANIZED_DIR = Path(os.getenv('ORGANIZED_DIR', '/home/user/organized'))
INVENTORY_FILE = Path(os.getenv('INVENTORY_JSON', str(ORGANIZED_DIR / 'inventory.json')))
INVENTORY_CSV = Path(os.getenv('INVENTORY_CSV', str(ORGANIZED_DIR / 'inventory.csv')))
BACKUP_DIR = Path(os.getenv('BACKUP_DIR', str(ORGANIZED_DIR / 'backups')))

if not PRICECHARTING_API_KEY:
    print("PRICECHARTING_API_KEY not found in .env")
    exit(1)

def query_pricecharting(item_name, category=None, platform=None, max_results=None):
    """Query PriceCharting API - returns multiple potential matches"""
    if max_results is None:
        max_results = PRICECHARTING_MAX_RESULTS

    try:
        search_query = item_name

        if category == "Video Game Software" and platform:
            platform_map = {
                # Nintendo - Home Consoles
                "NES": "nes", "Famicom": "famicom",
                "SNES": "super-nintendo", "Super Nintendo": "super-nintendo",
                "Super Famicom": "super-famicom",
                "Nintendo 64": "nintendo-64", "N64": "nintendo-64",
                "GameCube": "gamecube", "Wii": "wii", "Wii U": "wii-u",
                "Switch": "nintendo-switch",
                # Nintendo - Handhelds
                "Game Boy": "gameboy", "Game Boy Color": "gameboy-color",
                "Game Boy Advance": "gameboy-advance",
                "Nintendo DS": "nintendo-ds", "Nintendo 3DS": "nintendo-3ds",
                "Virtual Boy": "virtual-boy",
                # PlayStation
                "PlayStation": "playstation", "PS1": "playstation",
                "PlayStation 2": "playstation-2", "PS2": "playstation-2",
                "PlayStation 3": "playstation-3", "PS3": "playstation-3",
                "PlayStation 4": "playstation-4", "PS4": "playstation-4",
                "PlayStation 5": "playstation-5", "PS5": "playstation-5",
                "PSP": "psp", "PS Vita": "playstation-vita",
                # Xbox
                "Xbox": "xbox", "Xbox 360": "xbox-360",
                "Xbox One": "xbox-one", "Xbox Series X": "xbox-series-x",
                # Sega
                "Sega Genesis": "sega-genesis", "Genesis": "sega-genesis",
                "Mega Drive": "sega-mega-drive",
                "Sega Master System": "sega-master-system",
                "Game Gear": "game-gear",
                "Sega Saturn": "sega-saturn", "Saturn": "sega-saturn",
                "Sega Dreamcast": "sega-dreamcast", "Dreamcast": "sega-dreamcast",
                "Sega CD": "sega-cd", "Sega 32X": "sega-32x",
                # SNK / Neo Geo
                "Neo Geo AES": "neo-geo", "Neo Geo MVS": "neo-geo",
                "Neo Geo Pocket": "neo-geo-pocket",
                "Neo Geo Pocket Color": "neo-geo-pocket-color",
                # NEC
                "TurboGrafx-16": "turbografx-16", "PC Engine": "pc-engine",
                # Bandai
                "WonderSwan": "wonderswan", "WonderSwan Color": "wonderswan-color",
                # Other
                "3DO": "3do", "CDi": "cd-i",
                "Atari 2600": "atari-2600", "Atari 7800": "atari-7800",
                "Atari Jaguar": "atari-jaguar", "Atari Lynx": "atari-lynx",
            }
            pc_platform = platform_map.get(platform, platform.lower().replace(" ", "-"))
            search_query = f"{item_name} {pc_platform}"
        elif category == "LEGO":
            search_query = f"lego {item_name}"
        elif category == "Comic Books":
            search_query = f"comic {item_name}"
        elif category == "Trading Cards":
            search_query = item_name

        # Search
        params = {"t": PRICECHARTING_API_KEY, "q": search_query}
        response = requests.get("https://www.pricecharting.com/api/products", params=params, timeout=10)

        if not response.ok:
            return None

        data = response.json()
        products = data.get('products', [])

        if not products:
            return None

        # Get details for top results
        pricing_results = []
        for product in products[:max_results]:
            detail_response = requests.get(
                "https://www.pricecharting.com/api/product",
                params={"t": PRICECHARTING_API_KEY, "id": product.get('id')},
                timeout=10
            )

            if detail_response.ok:
                detail = detail_response.json()
                pricing = {
                    "source": "PriceCharting",
                    "product_name": detail.get('product-name'),
                    "category": detail.get('console-name') or detail.get('category'),
                    "loose_price": detail.get('loose-price'),
                    "cib_price": detail.get('cib-price'),
                    "new_price": detail.get('new-price'),
                    "used_price": detail.get('used-price'),
                    "genre": detail.get('genre'),
                    "release_date": detail.get('release-date'),
                    "upc": detail.get('upc'),
                    "product_url": f"https://www.pricecharting.com/game/{detail.get('id')}",
                    "last_updated": datetime.now().isoformat()
                }
                pricing_results.append(pricing)

        return pricing_results if pricing_results else None

    except Exception as e:
        print(f"    PriceCharting error: {e}")
        return None

def is_pricecharting_eligible(item):
    """Check if item should be looked up in PriceCharting"""
    if item['status'] != 'success':
        return False

    category = item['ai_analysis'].get('category', '')

    return category in ['Video Game Software', 'Video Game Console',
                       'Video Game Accessory', 'LEGO', 'Comic Books',
                       'Trading Cards']

def select_best_price(item, pricecharting_results):
    """
    Intelligently select best price from PriceCharting results
    Returns: (selected_price, selected_option_index, confidence)
    """
    if not pricecharting_results:
        return None, None, "NONE"

    pricing_basis = item['ai_analysis'].get('pricing_basis', '')

    # Simple heuristic: use first result and appropriate price field
    best_match = pricecharting_results[0]

    if pricing_basis == "LOOSE_CART" and best_match.get('loose_price'):
        return best_match['loose_price'], 0, "HIGH"
    elif pricing_basis == "COMPLETE_IN_BOX" and best_match.get('cib_price'):
        return best_match['cib_price'], 0, "HIGH"
    elif pricing_basis == "NEW_SEALED" and best_match.get('new_price'):
        return best_match['new_price'], 0, "HIGH"
    elif best_match.get('used_price'):
        return best_match['used_price'], 0, "MEDIUM"
    elif best_match.get('loose_price'):
        return best_match['loose_price'], 0, "MEDIUM"

    return None, None, "LOW"

def update_inventory(dry_run=False, new_only=False, categories=None):
    """Update inventory with PriceCharting data"""

    # Load inventory
    if not INVENTORY_FILE.exists():
        print(f"Inventory not found: {INVENTORY_FILE}")
        return

    with open(INVENTORY_FILE, 'r') as f:
        inventory = json.load(f)

    # Backup original
    if not dry_run:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_file = BACKUP_DIR / f"inventory_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(backup_file, 'w') as f:
            json.dump(inventory, f, indent=2)
        print(f"Backed up to: {backup_file}\n")

    # Filter items to update
    items_to_update = []
    for idx, item in enumerate(inventory):
        if not is_pricecharting_eligible(item):
            continue

        # Check category filter
        if categories and item['ai_analysis'].get('category') not in categories:
            continue

        # Check if already has PriceCharting data
        if new_only and item.get('pricecharting_data'):
            continue

        items_to_update.append((idx, item))

    if not items_to_update:
        print("No items need PriceCharting updates")
        return

    print(f"Found {len(items_to_update)} items to update")
    print(f"{'='*70}\n")

    updated_count = 0
    failed_count = 0
    api_calls = 0

    for idx, item in items_to_update:
        item_num = idx + 1
        item_name = item['item_name']
        tote_id = item['tote_id']
        category = item['ai_analysis'].get('category')
        platform = item['ai_analysis'].get('platform')

        print(f"[{item_num}/{len(inventory)}] {item_name} ({tote_id})")

        if dry_run:
            print(f"  [DRY RUN] Would query PriceCharting for: {category}")
            continue

        # Query PriceCharting
        print(f"  Querying PriceCharting...")
        pc_results = query_pricecharting(item_name, category, platform)
        api_calls += 1

        if not pc_results:
            print(f"  Not found")
            failed_count += 1
            continue

        # Select best price
        best_price, option_idx, confidence = select_best_price(item, pc_results)

        if best_price:
            old_price = item['ai_analysis']['estimated_value_usd']
            print(f"  Found: {pc_results[0]['product_name']}")
            print(f"     Old: ${old_price:.2f} -> New: ${best_price:.2f}")

            # Update inventory item
            inventory[idx]['pricecharting_data'] = pc_results
            inventory[idx]['pricecharting_updated_at'] = datetime.now().isoformat()
            inventory[idx]['ai_analysis']['estimated_value_usd'] = best_price
            inventory[idx]['ai_analysis']['price_source'] = f"PriceCharting (option {option_idx+1})"
            inventory[idx]['ai_analysis']['pricecharting_match_used'] = option_idx + 1
            inventory[idx]['ai_analysis']['pricecharting_match_confidence'] = confidence

            updated_count += 1
        else:
            print(f"  No appropriate price found")
            failed_count += 1

    if dry_run:
        print(f"\n[DRY RUN] Would update {len(items_to_update)} items")
        return

    # Save updated inventory
    with open(INVENTORY_FILE, 'w') as f:
        json.dump(inventory, f, indent=2)

    # Regenerate CSV
    with open(INVENTORY_CSV, 'w') as f:
        f.write("tote_id,item_sequence,item_name,category,estimated_value_usd,confidence,manual_review,status\n")
        for item in inventory:
            if item['status'] == 'success':
                ai = item['ai_analysis']
                review = "YES" if ai.get('manual_review_recommended') else "NO"
                f.write(f'"{item["tote_id"]}",{item.get("item_sequence",0)},"{item["item_name"]}","{ai.get("category","")}",{ai.get("estimated_value_usd",0)},"{ai.get("confidence","")}",{review},success\n')

    # Summary
    total_value = sum(item['ai_analysis']['estimated_value_usd']
                     for item in inventory if item['status'] == 'success')

    print(f"\n{'='*70}")
    print("UPDATE COMPLETE")
    print(f"{'='*70}")
    print(f"API calls made: {api_calls}")
    print(f"Successfully updated: {updated_count}")
    print(f"Failed to find: {failed_count}")
    print(f"Total collection value: ${total_value:,.2f}")
    print(f"Updated inventory: {INVENTORY_FILE}")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Update inventory with PriceCharting data')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be updated without making changes')
    parser.add_argument('--new-only', action='store_true', help='Only update items without existing PriceCharting data')
    parser.add_argument('--categories', nargs='+', help='Only update specific categories')

    args = parser.parse_args()

    print("="*70)
    print("PRICECHARTING PRICE UPDATER")
    print("="*70)

    if args.dry_run:
        print("MODE: Dry run (no changes will be made)")
    if args.new_only:
        print("FILTER: New items only (skip items with existing PriceCharting data)")
    if args.categories:
        print(f"FILTER: Categories: {', '.join(args.categories)}")

    print("="*70 + "\n")

    if not args.dry_run:
        confirm = input("This will update your inventory. Continue? (y/n): ")
        if confirm.lower() != 'y':
            print("Cancelled.")
            exit(0)

    update_inventory(
        dry_run=args.dry_run,
        new_only=args.new_only,
        categories=args.categories
    )
