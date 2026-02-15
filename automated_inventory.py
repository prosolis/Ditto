#!/usr/bin/env python3
"""
AUTOMATED INVENTORY SCANNER
============================

Live scanner that watches a directory for new scans from Czur book scanner.
Automatically identifies items, organizes files, and builds inventory.

SETUP:
------
1. Install dependencies:
   pip install requests python-dotenv watchdog pyzbar Pillow --break-system-packages

2. Copy .env.example to .env and configure:
   cp .env.example .env
   nano .env

3. Start supporting services:
   Terminal 1: cd <SCAN_DIR parent> && python3 -m http.server 8000
   Terminal 2: ngrok http 8000  (copy the https URL to .env)
   Terminal 3: ollama run qwen2.5:32b

USAGE:
------
python inventory_scanner.py

Then use Czur scanner:
1. Scan tote QR label → Creates tote directory, sets context
2. Scan items → Auto-identified, renamed, organized
3. Repeat for next tote

WORKFLOW:
---------
Scan: TOTE-001 QR code
  → Creates /organized/TOTE-001/
  → Sets current tote context

Scan: Item photo
  → Google Lens identifies item
  → PriceCharting lookup (if video game/LEGO/comic)
  → LLM synthesizes results with regional awareness
  → Auto-cropped to remove black mat (if enabled)
  → Renamed: Beautiful_Katamari_TOTE-001.jpg
  → Moved to /organized/TOTE-001/
  → Added to inventory.json + inventory.csv

OUTPUT:
-------
/organized/TOTE-001/Item_Name_TOTE-001.jpg
/organized/TOTE-002/Item_Name_TOTE-002.jpg
/organized/inventory.json  # Full detailed inventory
/organized/inventory.csv   # Spreadsheet summary

SUPPORTED FORMATS:
------------------
jpg, jpeg, png, webp, gif, bmp, tiff, tif
"""

import requests
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from pyzbar.pyzbar import decode
from PIL import Image
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Load environment variables
load_dotenv()

# ========================================
# CONFIGURATION FROM .ENV
# ========================================

# API Configuration
SERPAPI_KEY = os.getenv('SERPAPI_KEY')
PRICECHARTING_API_KEY = os.getenv('PRICECHARTING_API_KEY')

# Network Configuration
NGROK_URL = os.getenv('NGROK_URL', 'https://your-ngrok-url.ngrok-free.app')

# Directory Configuration
SCAN_DIR = Path(os.getenv('SCAN_DIR', '/path/to/czur/output'))
ORGANIZED_DIR = Path(os.getenv('ORGANIZED_DIR', '/home/user/organized'))
HTTP_SERVER_ROOT = SCAN_DIR.parent

# Ollama Configuration
OLLAMA_ENDPOINT = os.getenv('OLLAMA_ENDPOINT', 'http://localhost:11434/api/generate')
LLM_MODEL = os.getenv('LLM_MODEL', 'qwen2.5:32b')
OLLAMA_TIMEOUT = int(os.getenv('OLLAMA_TIMEOUT', '120'))

# Processing Settings
AUTOCROP_ENABLED = os.getenv('AUTOCROP_ENABLED', 'true').lower() == 'true'
AUTOCROP_FUZZ = int(os.getenv('AUTOCROP_FUZZ', '10'))
PRICECHARTING_MAX_RESULTS = int(os.getenv('PRICECHARTING_MAX_RESULTS', '5'))
VERBOSE_LOGGING = os.getenv('VERBOSE_LOGGING', 'false').lower() == 'true'
MAX_RETRIES = int(os.getenv('MAX_RETRIES', '2'))

# Validate required settings
if not SERPAPI_KEY:
    raise ValueError("SERPAPI_KEY not found in .env file")

if "your-ngrok-url" in NGROK_URL:
    raise ValueError("NGROK_URL not configured in .env file - update with your actual ngrok URL")

# Supported image formats
SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif'}

# ========================================
# UTILITY FUNCTIONS
# ========================================

def sanitize_filename(text):
    """Convert text to filesystem-safe format"""
    text = text.replace(" ", "_")
    text = text.replace("'", "")
    text = text.replace(":", "")
    text = re.sub(r'[^\w\-_]', '', text)
    return text

def local_path_to_url(image_path):
    """Convert local file path to ngrok URL"""
    relative_path = Path(image_path).relative_to(HTTP_SERVER_ROOT)
    url_path = str(relative_path).replace('\\', '/')
    return f"{NGROK_URL}/{url_path}"

def check_for_tote_qr(image_path):
    """Check if scan is a tote ID QR code.

    Accepts any QR payload containing a TOTE-XXX identifier:
      - Plain text: "TOTE-001"
      - JSON with tote_id field: {"tote_id": "TOTE-001", ...}
      - Text containing a tote ID: "Inventory TOTE-001 label"
    """
    try:
        decoded = decode(Image.open(image_path))

        if not decoded:
            if VERBOSE_LOGGING:
                print(f"    [QR] No QR code detected in {image_path.name}")
            return {"is_tote_qr": False}

        qr_data = decoded[0].data.decode()
        if VERBOSE_LOGGING:
            print(f"    [QR] Raw data: {qr_data}")

        tote_id = None

        # Try JSON first (may contain a tote_id field)
        try:
            data = json.loads(qr_data)
            if isinstance(data, dict) and 'tote_id' in data:
                tote_id = data['tote_id']
        except (json.JSONDecodeError, ValueError):
            pass

        # Fall back to regex search for TOTE-XXX anywhere in the payload
        if not tote_id:
            match = re.search(r'(TOTE-\d+)', qr_data)
            if match:
                tote_id = match.group(1)

        if not tote_id:
            if VERBOSE_LOGGING:
                print(f"    [QR] No TOTE-XXX pattern found in: {qr_data}")
            return {"is_tote_qr": False}

        return {
            "is_tote_qr": True,
            "tote_id": tote_id,
            "tote_id_safe": sanitize_filename(tote_id)
        }

    except Exception as e:
        if VERBOSE_LOGGING:
            print(f"    [QR] Error: {e}")
        return {"is_tote_qr": False}

# ========================================
# IMAGE PROCESSING
# ========================================

def autocrop_image(image_path):
    """Auto-crop image to remove black mat background"""
    if not AUTOCROP_ENABLED:
        return True
    
    try:
        subprocess.run([
            'convert', str(image_path),
            '-fuzz', f'{AUTOCROP_FUZZ}%',
            '-trim',
            '+repage',
            str(image_path)
        ], check=True, capture_output=True)
        
        if VERBOSE_LOGGING:
            print(f"    ✂️  Auto-cropped")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"    ⚠️  Crop failed: {e.stderr.decode()}")
        return False

# ========================================
# GOOGLE LENS INTEGRATION
# ========================================

def reverse_image_search(image_path):
    """Send image to Google Lens via SerpAPI"""
    image_url = local_path_to_url(image_path)
    
    params = {
        "engine": "google_lens",
        "api_key": SERPAPI_KEY,
        "url": image_url
    }
    
    response = requests.get("https://serpapi.com/search", params=params, timeout=30)
    
    if not response.ok:
        raise Exception(f"SerpAPI error: {response.status_code}")
    
    return response.json()

def format_search_results(search_data):
    """Extract relevant info from Google results"""
    context = "=== GOOGLE IMAGE SEARCH RESULTS ===\n\n"
    
    visual_matches = search_data.get('visual_matches', [])
    if visual_matches:
        context += "VISUALLY SIMILAR ITEMS:\n"
        for idx, match in enumerate(visual_matches[:15], 1):
            context += f"\n{idx}. {match.get('title', 'Unknown')}\n"
            context += f"   Source: {match.get('link', 'N/A')}\n"
            if 'price' in match:
                price_val = match['price'].get('extracted_value', match['price'].get('value', 'N/A'))
                currency = match['price'].get('currency', '')
                context += f"   Price: {price_val} {currency}\n"
            if 'condition' in match:
                context += f"   Condition: {match['condition']}\n"
            if 'rating' in match:
                context += f"   Rating: {match['rating']} ({match.get('reviews', 0)} reviews)\n"
        context += "\n"
    
    return context

# ========================================
# PRICECHARTING INTEGRATION
# ========================================

def should_check_pricecharting(google_results):
    """Determine if we should query PriceCharting based on Google results"""
    if not PRICECHARTING_API_KEY:
        return False, None, None, None
    
    visual_matches = google_results.get('visual_matches', [])
    
    if not visual_matches:
        return False, None, None, None
    
    first_match = visual_matches[0].get('title', '').lower()
    
    # Video game indicators
    game_platforms = ["xbox", "xbox 360", "xbox one", "xbox series",
                     "playstation", "ps1", "ps2", "ps3", "ps4", "ps5", "psp", "ps vita", "vita",
                     "wii", "wii u", "switch", "nes", "famicom", "snes", "super famicom",
                     "n64", "gamecube", "game boy", "gameboy", "gba", "game boy advance",
                     "game boy color", "gbc", "ds", "3ds", "virtual boy",
                     "genesis", "mega drive", "master system", "game gear",
                     "saturn", "dreamcast", "sega cd", "sega 32x",
                     "neo geo", "neo-geo", "neogeo", "aes", "mvs",
                     "neo geo pocket", "wonderswan",
                     "turbografx", "pc engine", "pc-engine",
                     "3do", "cdi", "cd-i", "atari"]
    game_keywords = ["game", "video game", "cartridge"]
    
    is_game = any(platform in first_match for platform in game_platforms) or \
              any(keyword in first_match for keyword in game_keywords)
    
    # LEGO indicators
    is_lego = "lego" in first_match or ("set" in first_match and any(x in first_match for x in ["brick", "minifig", "star wars", "creator"]))
    
    # Comic book indicators
    is_comic = "comic" in first_match or any(x in first_match for x in ["#", "issue", "marvel", "dc comics", "image comics"])

    # Trading card indicators
    is_card = any(x in first_match for x in [
        "pokemon", "magic the gathering", "mtg", "yu-gi-oh", "yugioh",
        "trading card", "tcg", "baseball card", "sports card",
        "topps", "panini", "upper deck", "psa", "beckett", "graded card"
    ])

    # DVD indicators
    is_dvd = any(x in first_match for x in [
        "dvd", "widescreen edition", "fullscreen edition",
        "special edition dvd", "collector's edition dvd"
    ])

    # Blu-ray indicators
    is_bluray = any(x in first_match for x in [
        "blu-ray", "blu ray", "bluray", "4k uhd", "4k ultra hd",
        "steelbook", "criterion collection"
    ])

    # Audio CD indicators
    is_cd = any(x in first_match for x in [
        "audio cd", "music cd", "cd album", "compact disc",
        "vinyl", "lp record", "phonograph"
    ]) or ("cd" in first_match and any(x in first_match for x in [
        "album", "deluxe edition", "remastered", "soundtrack",
        "greatest hits", "discography"
    ]))

    # Book indicators (non-comic)
    is_book = any(x in first_match for x in [
        "hardcover", "paperback", "hardback", "softcover",
        "isbn", "novel", "edition book", "first edition",
        "textbook", "audiobook", "manga"
    ]) or ("book" in first_match and not is_comic)

    if not (is_game or is_lego or is_comic or is_card or is_dvd or is_bluray or is_cd or is_book):
        return False, None, None, None
    
    # Extract potential name and details
    potential_name = visual_matches[0].get('title', '').split('-')[0].strip()
    
    category = None
    platform = None
    
    if is_game:
        category = "Video Game Software"
        # Try to detect platform
        for plat in ["Xbox Series X", "Xbox One", "Xbox 360", "Xbox",
                     "PS5", "PS4", "PS3", "PS2", "PS1", "PSP", "PS Vita",
                     "Switch", "Wii U", "Wii", "GameCube", "N64", "SNES", "NES",
                     "Virtual Boy",
                     "Game Boy Advance", "Game Boy Color", "Game Boy",
                     "Nintendo 3DS", "Nintendo DS",
                     "Genesis", "Sega Master System", "Game Gear",
                     "Sega Saturn", "Sega Dreamcast", "Sega CD", "Sega 32X",
                     "Neo Geo AES", "Neo Geo MVS", "Neo Geo Pocket Color", "Neo Geo Pocket",
                     "WonderSwan Color", "WonderSwan",
                     "TurboGrafx-16", "PC Engine",
                     "3DO", "CDi",
                     "Atari 2600", "Atari 7800", "Atari Jaguar", "Atari Lynx"]:
            if plat.lower() in first_match:
                platform = plat
                break
    elif is_lego:
        category = "LEGO"
    elif is_comic:
        category = "Comic Books"
    elif is_card:
        category = "Trading Cards"
    elif is_bluray:
        category = "Blu-ray"
    elif is_dvd:
        category = "DVD"
    elif is_cd:
        category = "CD"
    elif is_book:
        category = "Books"
    
    return True, potential_name, category, platform

def query_pricecharting(item_name, category=None, platform=None):
    """Query PriceCharting API - returns multiple potential matches"""
    if not PRICECHARTING_API_KEY:
        return None
    
    try:
        # Build search query
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
        elif category == "DVD":
            search_query = f"dvd {item_name}"
        elif category == "Blu-ray":
            search_query = f"blu-ray {item_name}"
        elif category == "CD":
            search_query = f"cd {item_name}"
        elif category == "Books":
            search_query = f"book {item_name}"

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
        for product in products[:PRICECHARTING_MAX_RESULTS]:
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
                    "product_url": f"https://www.pricecharting.com/game/{detail.get('id')}"
                }
                pricing_results.append(pricing)
        
        if pricing_results:
            print(f"  💰 PriceCharting: {len(pricing_results)} matches")
            return pricing_results
        
        return None
        
    except Exception as e:
        print(f"  ⚠️  PriceCharting error: {e}")
        return None

# ========================================
# LLM VALIDATION
# ========================================

def validate_inventory_item(data, num_pricecharting_results=0):
    """Validate LLM JSON output matches expected schema"""
    
    required_fields = [
        'item_name', 'confidence', 'confidence_reason',
        'estimated_value_usd', 'price_source', 'pricing_basis', 'category'
    ]
    
    # Check required fields
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"Missing required fields: {missing}")
    
    # Validate confidence
    if data['confidence'] not in ['HIGH', 'MEDIUM', 'LOW']:
        if data['confidence'] is None:
            data['confidence'] = 'LOW'
            print(f"    ⚠️  LLM returned null confidence, defaulting to LOW")
        else:
            raise ValueError(f"Invalid confidence: '{data['confidence']}' (must be HIGH/MEDIUM/LOW)")

    # Validate pricing_basis (with auto-fix for LLM indecision)
    valid_pricing = [
        'COMPLETE_IN_BOX', 'LOOSE_CART', 'LOOSE_DISC', 'NEW_SEALED',
        'LOOSE_ACCESSORY', 'CONSOLE_ONLY', 'COMPLETE_CONSOLE',
        'HANDHELD_ONLY', 'COMPLETE_HANDHELD', 'USED'
    ]

    pricing_basis = data['pricing_basis']

    if pricing_basis is None:
        data['pricing_basis'] = 'USED'
        data['manual_review_recommended'] = True
        if not data.get('manual_review_reason'):
            data['manual_review_reason'] = "LLM could not determine condition - please verify"
        print(f"    ⚠️  LLM returned null pricing_basis, defaulting to USED and flagging for review")
        pricing_basis = data['pricing_basis']

    # Handle LLM indecision (e.g., "COMPLETE_IN_BOX/LOOSE_CART")
    if '/' in pricing_basis:
        print(f"    ⚠️  LLM uncertain about condition: '{pricing_basis}'")
        pricing_basis = pricing_basis.split('/')[0].strip()
        data['pricing_basis'] = pricing_basis
        data['manual_review_recommended'] = True
        if not data.get('manual_review_reason'):
            data['manual_review_reason'] = "LLM uncertain about condition - please verify"
        print(f"    ⚠️  Using '{pricing_basis}' and flagging for manual review")
    
    if data['pricing_basis'] not in valid_pricing:
        raise ValueError(f"Invalid pricing_basis: '{data['pricing_basis']}' (must be one of {valid_pricing})")

    # Enforce platform-based condition defaults
    # LLM often overrides COMPLETE_IN_BOX with LOOSE_DISC/LOOSE_CART based on search result titles
    platform = (data.get('platform') or '').lower()
    category = (data.get('category') or '').lower()
    pricing_basis = data['pricing_basis']

    cib_mandatory_platforms = [
        'playstation', 'ps1', 'ps2', 'ps3', 'ps4', 'ps5',
        'xbox', 'xbox 360', 'xbox one', 'xbox series',
        'gamecube', 'wii', 'wii u',
        'sega cd', 'saturn', 'dreamcast',
        'pc', '3do', 'cdi', 'pc engine cd',
        'ds', 'nintendo ds', '3ds', 'nintendo 3ds',
        'switch', 'nintendo switch', 'ps vita', 'vita',
    ]

    platform_should_be_cib = any(p in platform for p in cib_mandatory_platforms)

    # Also catch category-level disc-based items
    if not platform_should_be_cib and category == 'video game software' and platform:
        disc_keywords = ['playstation', 'xbox', 'gamecube', 'dreamcast', 'saturn', 'sega cd', 'wii', '3do', 'cdi']
        platform_should_be_cib = any(kw in platform for kw in disc_keywords)

    if platform_should_be_cib and pricing_basis in ('LOOSE_DISC', 'LOOSE_CART'):
        original = pricing_basis
        data['pricing_basis'] = 'COMPLETE_IN_BOX'
        data['manual_review_recommended'] = True
        if not data.get('manual_review_reason'):
            data['manual_review_reason'] = f"LLM set {original} but platform '{data.get('platform')}' defaults to COMPLETE_IN_BOX - please verify"
        print(f"    ⚠️  Auto-correcting pricing_basis: {original} → COMPLETE_IN_BOX (platform default for '{data.get('platform')}')")

    # Validate numeric fields
    numeric_fields = ['estimated_value_usd', 'value_range_min', 'value_range_max']
    for field in numeric_fields:
        if field in data:
            if not isinstance(data[field], (int, float)):
                raise ValueError(f"{field} must be number, got {type(data[field])}")
            if data[field] < 0:
                raise ValueError(f"{field} cannot be negative: {data[field]}")
    
    # Auto-fix swapped value ranges
    if data.get('value_range_min', 0) > data.get('value_range_max', 0):
        print(f"    ⚠️  Auto-fixing swapped value range: min={data['value_range_min']} max={data['value_range_max']}")
        data['value_range_min'], data['value_range_max'] = data['value_range_max'], data['value_range_min']
    
    # Validate booleans
    bool_fields = ['personal_effect_eligible', 'manual_review_recommended']
    for field in bool_fields:
        if field in data and not isinstance(data[field], bool):
            raise ValueError(f"{field} must be boolean, got {type(data[field])}")
    
    # Validate arrays
    if 'warnings' in data and not isinstance(data['warnings'], list):
        raise ValueError("warnings must be array")
    
    # Validate item_name is not empty - attempt recovery instead of hard failure
    if not data.get('item_name') or not isinstance(data.get('item_name'), str) or data['item_name'].strip() == '':
        data['item_name'] = 'Unidentified Item'
        data['manual_review_recommended'] = True
        if not data.get('manual_review_reason'):
            data['manual_review_reason'] = "LLM could not identify item name - please review and rename"
        print(f"    ⚠️  LLM returned null/empty item_name, using placeholder and flagging for review")
    
    # Validate new optional fields (grade, issue_number, grader, year)
    if data.get('issue_number') is not None and not isinstance(data['issue_number'], str):
        # Auto-convert integers to strings
        data['issue_number'] = str(data['issue_number'])

    if data.get('year') is not None:
        if not isinstance(data['year'], (int, float)):
            raise ValueError(f"year must be integer or null, got {type(data['year'])}")
        data['year'] = int(data['year'])

    if data.get('grade') is not None:
        if not isinstance(data['grade'], (int, float)):
            raise ValueError(f"grade must be number or null, got {type(data['grade'])}")

    if data.get('grader') is not None and not isinstance(data['grader'], str):
        raise ValueError(f"grader must be string or null, got {type(data['grader'])}")

    # Validate pricecharting_match_used if present (with auto-fix for hallucinated values)
    max_valid = num_pricecharting_results if num_pricecharting_results > 0 else PRICECHARTING_MAX_RESULTS
    if data.get('pricecharting_match_used') is not None:
        match_value = data['pricecharting_match_used']

        if not isinstance(match_value, int):
            raise ValueError(f"pricecharting_match_used must be integer or null, got {type(match_value)}")

        # Auto-fix: no PriceCharting results were provided but LLM set a value
        if num_pricecharting_results == 0:
            print(f"    ⚠️  pricecharting_match_used={match_value} but no PriceCharting options were provided")
            print(f"    ⚠️  Setting to null - LLM hallucinated option number")
            data['pricecharting_match_used'] = None
            data['pricecharting_match_confidence'] = 'NONE'
        # Auto-fix out-of-range values
        elif match_value < 1 or match_value > max_valid:
            print(f"    ⚠️  Invalid pricecharting_match_used: {match_value} (must be 1-{max_valid})")
            print(f"    ⚠️  Setting to null - LLM hallucinated option number")
            data['pricecharting_match_used'] = None
            data['manual_review_recommended'] = True
            if not data.get('manual_review_reason'):
                data['manual_review_reason'] = "LLM provided invalid PriceCharting match - please verify pricing"
    
    return True

# ========================================
# LLM JSON SANITIZATION
# ========================================

def sanitize_llm_json(text):
    """Fix common JSON issues in LLM output that cause parse failures.

    Handles: control characters, trailing commas, inline comments,
    math expressions in numeric values, and truncated responses.
    """
    # Remove control characters (except tab, newline, carriage return)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)

    # Remove trailing commas before } or ] (common LLM mistake)
    text = re.sub(r',\s*([}\]])', r'\1', text)

    # Remove single-line comments (LLM sometimes adds // comments in JSON)
    # But only outside of string values - look for // not inside quotes
    # Simple heuristic: // at start of line or after , or after {
    text = re.sub(r'(?<=,)\s*//[^\n]*', '', text)
    text = re.sub(r'(?<=\{)\s*//[^\n]*', '', text)

    # Evaluate inline math expressions in numeric value positions
    # e.g. "value_range_max": 45954.0 / 137  ->  "value_range_max": 335.43
    def _eval_math(m):
        expr = m.group(1).strip()
        try:
            # Only allow simple arithmetic: numbers and +-*/
            if re.match(r'^[\d\.\s\+\-\*/\(\)]+$', expr):
                result = round(float(eval(expr)), 2)
                return f': {result}'
        except Exception:
            pass
        return m.group(0)

    text = re.sub(r':\s*([\d\.\s\+\-\*/\(\)]+(?:[+\-\*/]\s*[\d\.]+)+)\s*(?=[,}\n\r])',
                  _eval_math, text)

    # Fix truncated JSON - if the response was cut off mid-stream
    # Check if braces/brackets are balanced
    text = _repair_truncated_json(text)

    return text


def _repair_truncated_json(text):
    """Attempt to close a truncated JSON response.

    If the LLM hit its output token limit, the JSON may be cut off
    mid-string or mid-object.  This tries to close it gracefully.
    """
    # Quick check: is the JSON already balanced?
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')

    if open_braces <= 0 and open_brackets <= 0:
        return text  # Already balanced or not truncated

    stripped = text.rstrip()

    # If we're inside a string value (odd number of unescaped quotes
    # means an unclosed string), close it
    # Count unescaped quotes
    unescaped_quotes = len(re.findall(r'(?<!\\)"', stripped))
    if unescaped_quotes % 2 == 1:
        # Unclosed string - close it
        stripped += '"'

    # Remove any trailing partial key-value (e.g., truncated after a comma
    # with no value, or a key with no colon)
    # If it ends with ',' or ':' after our string closure, trim it
    stripped = re.sub(r'[,:]\s*$', '', stripped)

    # Close any open brackets then braces
    for _ in range(open_brackets):
        stripped += ']'
    for _ in range(open_braces):
        stripped += '}'

    return stripped


def repair_json_at_error(text, max_attempts=5):
    """Iteratively repair JSON by escaping quotes at error positions.

    When json.loads fails with 'Expecting ',' delimiter', it usually means
    an unescaped double quote inside a string value prematurely closed the
    string.  This function escapes the problematic quote and retries.
    """
    for attempt in range(max_attempts):
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            err_msg = str(e)
            pos = e.pos

            if 'Expecting' in err_msg and ',' in err_msg and pos > 0:
                # The quote just before the error position likely closed
                # a string prematurely.  Find the last quote before pos
                # and escape it.
                last_quote = text.rfind('"', 0, pos)
                if last_quote > 0 and text[last_quote - 1:last_quote] != '\\':
                    text = text[:last_quote] + '\\"' + text[last_quote + 1:]
                    continue

            # For other errors, or if we couldn't find a quote to fix, give up
            raise

    return json.loads(text)

# ========================================
# LLM ANALYSIS
# ========================================

def analyze_with_llm(search_results_text, pricecharting_results=None):
    """LLM synthesis of Google + PriceCharting data with validation"""
    
    context = search_results_text
    
    if pricecharting_results:
        context += "\n\n=== PRICECHARTING MATCHES ===\n"
        context += f"Found {len(pricecharting_results)} potential matches. SELECT THE CORRECT REGIONAL VARIANT:\n\n"
        
        for idx, pc in enumerate(pricecharting_results, 1):
            context += f"OPTION {idx}: {pc['product_name']}\n"
            context += f"  Category/Platform: {pc['category']}\n"
            if pc.get('loose_price'):
                context += f"  Loose: ${pc['loose_price']}, CIB: ${pc.get('cib_price')}, New: ${pc.get('new_price')}\n"
            elif pc.get('used_price'):
                context += f"  Used: ${pc['used_price']}, New: ${pc.get('new_price')}\n"
            if pc.get('release_date'):
                context += f"  Release: {pc['release_date']}\n"
            if pc.get('upc'):
                context += f"  UPC: {pc['upc']}\n"
            context += f"  URL: {pc['product_url']}\n\n"
    else:
        context += "\n\n=== NO PRICECHARTING DATA AVAILABLE ===\n"
        context += "PriceCharting was not queried for this item. Set pricecharting_match_used to null.\n"

    prompt = f"""{context}

Analyze search results and return JSON:

{{
  "item_name": "Product title in photographed region's naming",
  "platform": "Gaming platform in photographed region's name",
  "region": "NTSC-U/PAL/NTSC-J or null",
  "region_reasoning": "Why you determined this region from text indicators",
  "confidence": "HIGH/MEDIUM/LOW",
  "confidence_reason": "Brief explanation",
  "estimated_value_usd": 0.00,
  "value_range_min": 0.00,
  "value_range_max": 0.00,
  "price_source": "Which sources used",
  "pricing_basis": "COMPLETE_IN_BOX/LOOSE_CART/LOOSE_DISC/NEW_SEALED/LOOSE_ACCESSORY/CONSOLE_ONLY/COMPLETE_CONSOLE/HANDHELD_ONLY/COMPLETE_HANDHELD/USED",
  "category": "Video Game Software, Video Game Console, Video Game Accessory, Handheld Game System, LEGO, Comic Books, Electronics, Collectibles, etc.",
  "comic_grade": null,
  "condition_notes": "Brief notes",
  "variant_notes": "Important variants, editions, regional differences",
  "personal_effect_eligible": true,
  "warnings": [],
  "pricecharting_match_used": {f"1-{len(pricecharting_results)} or null" if pricecharting_results else "null"},
  "pricecharting_match_confidence": "HIGH/MEDIUM/LOW/NONE",
  "manual_review_recommended": false,
  "manual_review_reason": "",
  "issue_number": "Comic issue or card number as string (e.g., '13', '4') or null",
  "year": "Publication or release year as integer (e.g., 1939, 2023) or null",
  "grade": "Numerical grade if professionally graded (e.g., 8, 9.5) or null",
  "grader": "Grading company (CGC, CBCS, PGX, PSA, BGS, SGC) or null"
}}

REGIONAL IDENTIFICATION (from Google search result TEXT):

Analyze TEXT in search results for regional indicators:

NTSC-J (Japan):
- Titles: "Japanese", "Japan", "NTSC-J", "Import", "JPN"
- Platforms: "Super Famicom", "PC Engine", "Mega Drive" (JP context)
- Names: "Rock Man" not "Mega Man", Japanese romanization
- Descriptions: "Japanese version", "Japan import"

NTSC-U (North America):
- Titles: "US", "USA", "NTSC-U", "North America"
- Platforms: "SNES", "TurboGrafx-16", "Genesis", "NES"
- Names: Standard English like "Mega Man"
- Descriptions: "US version", "ESRB rated"

PAL (Europe):
- Titles: "PAL", "European", "UK", "EU"
- Descriptions: "PEGI rated", "European version"

Use photographed item's regional naming, not US names for Japanese items.

REGIONAL NAMING:
- Mega Man (US) = Rock Man (JP)
- TurboGrafx-16 (US) = PC Engine (JP)
- Genesis (US) = Mega Drive (PAL/JP)
- Super Nintendo (US) = Super Famicom (JP)
- NES (US) = Famicom (JP)

CRITICAL - PRICING_BASIS RULES (you MUST follow these):

You CANNOT see the actual item. You are analyzing search results, NOT the item itself.
Search results often show "loose" or "pre-owned" listings because those are common marketplace listings.
The condition words in search result titles (e.g., "loose", "cart only", "disc only", "no manual")
describe OTHER sellers' listings, NOT the item being inventoried.

DO NOT change pricing_basis away from the platform default based on search result titles or conditions.
The platform-based pricing_basis below is MANDATORY unless an explicit override exception applies.

**8-BIT/16-BIT CARTRIDGES → LOOSE_CART (MANDATORY):**
- NES, SNES, Genesis, Master System, Game Boy/GBC/GBA, TurboGrafx-16, Atari
- Neo Geo AES/MVS, Neo Geo Pocket/Color, WonderSwan/Color, Virtual Boy, Game Gear

**DISC-BASED → COMPLETE_IN_BOX (MANDATORY):**
- PlayStation, Xbox, GameCube, Sega CD/Saturn/Dreamcast, PC games, Xbox 360
- 3DO, CDi, PC Engine CD, Wii U, Playstation 2, Playstation 3, Playstation 4
- Do NOT set LOOSE_DISC just because search results mention "loose" or "disc only"

**MODERN CARTRIDGES → COMPLETE_IN_BOX (MANDATORY):**
- DS, 3DS, Switch, PS Vita
- Do NOT set LOOSE_CART just because search results mention "loose" or "cart only"

**PHYSICAL MEDIA → COMPLETE_IN_BOX (MANDATORY):**
- DVD, Blu-ray, CD (case + disc + inserts/booklet is the standard)
- Do NOT set LOOSE_DISC just because search results mention "disc only"

**BOOKS → USED (DEFAULT):**
- Books do not have packaging-based conditions; use USED by default

**OVERRIDE EXCEPTIONS (only these justify changing from the mandatory default):**
- NEW_SEALED: ONLY if search results consistently say "factory sealed", "NIB", "unopened"
- LOOSE_ACCESSORY: For accessories without original packaging
- CONSOLE_ONLY, COMPLETE_CONSOLE, HANDHELD_ONLY, COMPLETE_HANDHELD: For console/handheld items based on descriptions

You must NOT use LOOSE_DISC or LOOSE_CART for platforms whose mandatory default is COMPLETE_IN_BOX.

PRICECHARTING MATCHING:
{"- " + str(len(pricecharting_results)) + " options are listed above. Select ONLY from those options (1-" + str(len(pricecharting_results)) + ") or null." if pricecharting_results else "- No PriceCharting data available. pricecharting_match_used MUST be null."}
- Match BOTH item name AND region
- Japanese cart → prefer Japanese listing
- US cart → prefer NTSC-U listing
- Set pricecharting_match_confidence: HIGH (clear match), MEDIUM (uncertain), LOW (questionable), NONE (no match)
- Use appropriate price: LOOSE_CART→loose_price, COMPLETE_IN_BOX→cib_price, NEW_SEALED→new_price
- If regional mismatch, set to null and warn

COMIC BOOK GRADING:
- comic_grade is a float on the 10-point scale (e.g., 8.0, 9.2, 9.8)
- Set comic_grade only for Comic Books category, null for everything else
- If grade is mentioned in search results, use that value
- If no grade info available, set to null

CATEGORIES:
Video Game Software, Video Game Console, Video Game Accessory, Handheld Game System, LEGO, Comic Books, Books, Trading Cards, DVD, Blu-ray, CD, Electronics, Collectibles

PERSONAL_EFFECT_ELIGIBLE:
- true: typical consumer items for personal use
- false: brand new sealed or luxury >$1000

MANUAL REVIEW:
- Flag if condition drastically affects value (10x+)
- Flag if regional variant uncertain
- Flag if conflicting Google results

GRADING AND NUMBERING (mostly for Comics and Trading Cards):
- issue_number: Comic issue number or trading card number (string). null for other categories.
- year: Publication or release year (integer). null if unknown or not applicable.
- grade: Numerical grade if item is professionally graded (e.g., 8, 9.5). null if ungraded.
- grader: Grading company name. null if ungraded.
  Comics: CGC, CBCS, PGX
  Trading Cards: PSA, BGS, SGC, CGC
- Look for grading slabs/cases, grade labels, and certification numbers in search results.
- If search results mention a grade or grading company, include them.

TRADING CARDS:
- Category "Trading Cards" for: Pokemon, Magic: The Gathering, Yu-Gi-Oh!, sports cards (baseball, basketball, football, hockey), etc.
- item_name should include the card name AND set name (e.g., "Charizard Pokemon Base Set")
- issue_number is the card number within the set
- platform field should be null for trading cards

DVD:
- Category "DVD" for: DVD movies, TV series box sets on DVD, special/collector's edition DVDs
- item_name should include the movie/show title and edition if notable (e.g., "The Matrix Widescreen Edition")
- platform field should be null for DVDs
- pricing_basis should be COMPLETE_IN_BOX (case + disc + inserts) by default

BLU-RAY:
- Category "Blu-ray" for: Blu-ray movies, 4K UHD Blu-ray, TV series on Blu-ray, steelbooks
- item_name should include the title and format if notable (e.g., "Blade Runner 2049 4K UHD", "Jaws Steelbook")
- Note "4K UHD" in item_name when applicable
- platform field should be null for Blu-rays
- pricing_basis should be COMPLETE_IN_BOX by default

CD:
- Category "CD" for: music albums, singles, soundtrack CDs, box sets
- item_name should include artist and album title (e.g., "Pink Floyd - The Dark Side of the Moon")
- platform field should be null for CDs
- pricing_basis should be COMPLETE_IN_BOX (jewel case + disc + booklet) by default

BOOKS:
- Category "Books" for: novels, non-fiction, textbooks, manga volumes, art books, reference books
- Do NOT use this for comic books (single issues or trade paperbacks) - use "Comic Books" instead
- item_name should include author and title (e.g., "Stephen King - The Shining")
- Note edition info if visible (e.g., "First Edition", "Signed Copy")
- platform field should be null for books
- pricing_basis should be USED by default
- issue_number can be used for manga volume numbers or book series numbers

Return ONLY valid JSON."""

    try:
        response = requests.post(
            OLLAMA_ENDPOINT,
            json={
                "model": LLM_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_ctx": 16384, "num_predict": 16384}
            },
            timeout=OLLAMA_TIMEOUT
        )
        
        if not response.ok:
            raise Exception(f"Ollama error: {response.status_code}")
        
        result = response.json()
        response_text = result.get('response', '').strip()
        
        if not response_text:
            raise Exception("Empty LLM response")
        
        # Strip markdown
        if '```json' in response_text:
            response_text = response_text.split('```json')[1].split('```')[0].strip()
        elif '```' in response_text:
            response_text = response_text.split('```')[1].split('```')[0].strip()

        # Sanitize JSON - fix common LLM output issues
        response_text = sanitize_llm_json(response_text)

        # Parse JSON - try multiple repair strategies if initial parse fails
        parsed = None
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError:
            # Strategy 1: Extract JSON object (handles trailing garbage text)
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                    if VERBOSE_LOGGING:
                        print(f"    ⚠️  Recovered JSON after stripping non-JSON content")
                except json.JSONDecodeError:
                    pass

            # Strategy 2: Fix unescaped quotes at error positions
            if parsed is None:
                try:
                    text_to_repair = json_match.group() if json_match else response_text
                    parsed = repair_json_at_error(text_to_repair)
                    if VERBOSE_LOGGING:
                        print(f"    ⚠️  Recovered JSON after escaping unescaped quotes")
                except (json.JSONDecodeError, Exception):
                    pass

        if parsed is None:
            if VERBOSE_LOGGING:
                print(f"    ⚠️  JSON parse error, raw response: {response_text[:500]}")
            raise Exception(f"LLM returned invalid JSON")

        # Try to recover item_name from search results if LLM returned null
        if not parsed.get('item_name') and search_results_text:
            title_match = re.search(r'VISUALLY SIMILAR ITEMS:\s*\n\s*1\.\s*(.+)', search_results_text)
            if title_match:
                fallback_name = title_match.group(1).strip()
                # Clean common marketplace suffixes
                fallback_name = re.sub(r'\s*[-|]\s*(eBay|Amazon|Walmart|GameStop|Target|Best Buy).*$', '', fallback_name, flags=re.IGNORECASE)
                if fallback_name:
                    parsed['item_name'] = fallback_name
                    if VERBOSE_LOGGING:
                        print(f"    ⚠️  Using top search result as item_name fallback: '{fallback_name}'")

        # Validate schema
        num_pc = len(pricecharting_results) if pricecharting_results else 0
        try:
            validate_inventory_item(parsed, num_pricecharting_results=num_pc)
        except ValueError as e:
            if VERBOSE_LOGGING:
                print(f"    ⚠️  Validation error: {e}")
                print(f"    Raw response: {response_text[:500]}")
            # Include item name in error for context
            item_hint = parsed.get('item_name', 'Unknown')
            raise Exception(f"Invalid LLM output for '{item_hint}': {e}")

        return parsed
    except Exception as e:
        raise Exception(f"LLM failed: {e}")

# ========================================
# ITEM PROCESSING
# ========================================

def process_item(image_path, tote_info, item_sequence):
    """Process single item scan with retry on transient errors"""
    print(f"\n  📦 Item #{item_sequence} in {tote_info['tote_id']}")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Google Lens
            print(f"    🔍 Google Lens...")
            search_results = reverse_image_search(image_path)
            formatted_results = format_search_results(search_results)

            # PriceCharting check
            should_check, potential_name, category, platform = should_check_pricecharting(search_results)
            pricecharting_results = None
            if should_check:
                print(f"    💰 PriceCharting ({category})...")
                pricecharting_results = query_pricecharting(potential_name, category, platform)

            # LLM analysis
            print(f"    🤖 Analyzing...")
            analysis = analyze_with_llm(formatted_results, pricecharting_results)

            # Auto-crop before organizing
            autocrop_image(image_path)

            # Organize file
            tote_dir = ORGANIZED_DIR / tote_info['tote_id_safe']
            tote_dir.mkdir(parents=True, exist_ok=True)

            item_name_safe = sanitize_filename(analysis['item_name'])
            new_filename = f"{item_name_safe}_{item_sequence:03d}_{tote_info['tote_id_safe']}{image_path.suffix}"
            new_path = tote_dir / new_filename

            shutil.move(image_path, new_path)

            print(f"    ✓ {analysis['item_name']} (${analysis['estimated_value_usd']:.2f})")
            if analysis.get('manual_review_recommended'):
                print(f"    ⚠️  MANUAL REVIEW: {analysis.get('manual_review_reason')}")

            return {
                "timestamp": datetime.now().isoformat(),
                "tote_id": tote_info['tote_id'],
                "item_sequence": item_sequence,
                "item_name": analysis['item_name'],
                "image_file": new_filename,
                "image_path": str(new_path),
                "ai_analysis": analysis,
                "pricecharting_data": pricecharting_results,
                "status": "success"
            }

        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError) as e:
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt
                print(f"    ⚠️  Network error: {e}")
                print(f"    🔄 Retrying in {wait}s (attempt {attempt}/{MAX_RETRIES})...")
                time.sleep(wait)
            else:
                print(f"    ❌ ERROR after {MAX_RETRIES} attempts: {e}")
                return {
                    "timestamp": datetime.now().isoformat(),
                    "tote_id": tote_info.get('tote_id', 'Unknown'),
                    "item_sequence": item_sequence,
                    "original_file": str(image_path),
                    "error": str(e),
                    "status": "failed"
                }

        except Exception as e:
            print(f"    ❌ ERROR: {e}")
            return {
                "timestamp": datetime.now().isoformat(),
                "tote_id": tote_info.get('tote_id', 'Unknown'),
                "item_sequence": item_sequence,
                "original_file": str(image_path),
                "error": str(e),
                "status": "failed"
            }

# ========================================
# FILE WATCHER
# ========================================

class InventoryScanner(FileSystemEventHandler):
    def __init__(self):
        self.current_tote = None
        self.item_sequence = 0
        self.inventory = []
        self.inventory_file = ORGANIZED_DIR / "inventory.json"
        self.csv_file = ORGANIZED_DIR / "inventory.csv"
        
        ORGANIZED_DIR.mkdir(parents=True, exist_ok=True)
        
        if self.inventory_file.exists():
            with open(self.inventory_file, 'r') as f:
                self.inventory = json.load(f)
        
        print("🚀 Inventory Scanner Ready")
        print(f"   Watching: {SCAN_DIR}")
        print(f"   Output: {ORGANIZED_DIR}")
        print("\n⏳ Waiting for scans...\n")
    
    def on_created(self, event):
        if event.is_directory:
            return
        
        file_path = Path(event.src_path)
        if file_path.suffix.lower() not in SUPPORTED_FORMATS:
            return
        
        time.sleep(1)  # Let file finish writing
        
        # Check for tote QR
        qr_result = check_for_tote_qr(file_path)
        
        if qr_result['is_tote_qr']:
            self.current_tote = {
                'tote_id': qr_result['tote_id'],
                'tote_id_safe': qr_result['tote_id_safe']
            }
            
            # Find max sequence for this tote (prevents duplicate sequences)
            tote_items = [item for item in self.inventory if item.get('tote_id') == qr_result['tote_id']]
            if tote_items:
                self.item_sequence = max(item.get('item_sequence', 0) for item in tote_items)
            else:
                self.item_sequence = 0
            
            print(f"\n{'='*70}")
            print(f"📦 NEW TOTE: {qr_result['tote_id']}")
            print(f"{'='*70}")
            
            tote_dir = ORGANIZED_DIR / qr_result['tote_id_safe']
            tote_dir.mkdir(parents=True, exist_ok=True)
            file_path.unlink()  # Delete QR scan
            print(f"   ✓ Ready for items (starting at #{self.item_sequence + 1})")
            
        else:
            if self.current_tote is None:
                print(f"\n⚠️  No tote selected! Scan a tote QR first.")
                return
            
            self.item_sequence += 1
            result = process_item(file_path, self.current_tote, self.item_sequence)
            self.inventory.append(result)
            self.save_inventory()
    
    def save_inventory(self):
        """Save JSON and CSV"""
        with open(self.inventory_file, 'w') as f:
            json.dump(self.inventory, f, indent=2)
        
        with open(self.csv_file, 'w') as f:
            f.write("tote_id,item_sequence,item_name,category,estimated_value_usd,confidence,manual_review,status\n")
            for item in self.inventory:
                if item['status'] == 'success':
                    ai = item['ai_analysis']
                    review = "YES" if ai.get('manual_review_recommended') else "NO"
                    f.write(f'"{item["tote_id"]}",{item["item_sequence"]},"{item["item_name"]}","{ai.get("category","")}",{ai.get("estimated_value_usd",0)},"{ai.get("confidence","")}",{review},success\n')
                else:
                    f.write(f'"{item.get("tote_id","")}",{item.get("item_sequence",0)},"","",0,"","",failed\n')

# ========================================
# MAIN
# ========================================

def main():
    print("="*70)
    print("AUTOMATED INVENTORY SYSTEM")
    print("="*70)
    print(f"\nConfiguration:")
    print(f"  Scans: {SCAN_DIR}")
    print(f"  Output: {ORGANIZED_DIR}")
    print(f"  Model: {LLM_MODEL}")
    print(f"  PriceCharting: {'Enabled' if PRICECHARTING_API_KEY else 'Disabled'}")
    print(f"  Auto-crop: {'Enabled' if AUTOCROP_ENABLED else 'Disabled'}")
    if AUTOCROP_ENABLED:
        print(f"  Crop fuzz: {AUTOCROP_FUZZ}%")
    print("\n" + "="*70 + "\n")
    
    input("Press Enter to start...")
    
    scanner = InventoryScanner()
    observer = Observer()
    observer.schedule(scanner, str(SCAN_DIR), recursive=False)
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n🛑 Stopping...")
        observer.stop()
        observer.join()
        
        successful = sum(1 for i in scanner.inventory if i['status'] == 'success')
        total_value = sum(i['ai_analysis']['estimated_value_usd'] 
                         for i in scanner.inventory if i['status'] == 'success')
        
        print(f"\n{'='*70}")
        print("FINAL SUMMARY")
        print(f"{'='*70}")
        print(f"Items: {len(scanner.inventory)}")
        print(f"Successful: {successful}")
        print(f"Total value: ${total_value:,.2f}")
        print(f"Inventory: {scanner.inventory_file}")
        print(f"{'='*70}\n")

if __name__ == "__main__":
    main()