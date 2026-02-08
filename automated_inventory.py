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
    """Check if scan is a tote ID QR code"""
    try:
        decoded = decode(Image.open(image_path))
        
        if not decoded:
            return {"is_tote_qr": False}
        
        qr_data = decoded[0].data.decode()
        data = json.loads(qr_data)
        
        # Validate structure
        if data.get('type') != 'PORTUGAL_MOVE_2026_TOTE':
            return {"is_tote_qr": False}
        
        if 'tote_id' not in data:
            return {"is_tote_qr": False}
        
        tote_id = data['tote_id']
        
        # Validate format
        if not re.match(r'^TOTE-\d{3}$', tote_id):
            return {"is_tote_qr": False}
        
        return {
            "is_tote_qr": True,
            "tote_id": tote_id,
            "tote_id_safe": sanitize_filename(tote_id)
        }
        
    except Exception as e:
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
    game_platforms = ["xbox", "playstation", "nintendo", "ps1", "ps2", "ps3", "ps4", "ps5", 
                     "wii", "switch", "nes", "snes", "n64", "gamecube", "genesis", "sega"]
    game_keywords = ["game", "video game", "cartridge"]
    
    is_game = any(platform in first_match for platform in game_platforms) or \
              any(keyword in first_match for keyword in game_keywords)
    
    # LEGO indicators
    is_lego = "lego" in first_match or ("set" in first_match and any(x in first_match for x in ["brick", "minifig", "star wars", "creator"]))
    
    # Comic book indicators
    is_comic = "comic" in first_match or any(x in first_match for x in ["#", "issue", "marvel", "dc comics", "image comics"])
    
    if not (is_game or is_lego or is_comic):
        return False, None, None, None
    
    # Extract potential name and details
    potential_name = visual_matches[0].get('title', '').split('-')[0].strip()
    
    category = None
    platform = None
    
    if is_game:
        category = "Video Game Software"
        # Try to detect platform
        for plat in ["Xbox 360", "Xbox One", "Xbox Series X", "PS5", "PS4", "PS3", "PS2", "PS1", 
                     "Switch", "Wii U", "Wii", "GameCube", "N64", "SNES", "NES", "Genesis"]:
            if plat.lower() in first_match:
                platform = plat
                break
    elif is_lego:
        category = "LEGO"
    elif is_comic:
        category = "Comic Books"
    
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
                "NES": "nes", "SNES": "super-nintendo", "Super Nintendo": "super-nintendo",
                "Nintendo 64": "nintendo-64", "N64": "nintendo-64",
                "GameCube": "gamecube", "Wii": "wii", "Wii U": "wii-u",
                "Switch": "nintendo-switch",
                "Game Boy": "gameboy", "Game Boy Color": "gameboy-color",
                "Game Boy Advance": "gameboy-advance",
                "Nintendo DS": "nintendo-ds", "Nintendo 3DS": "nintendo-3ds",
                "PlayStation": "playstation", "PS1": "playstation",
                "PlayStation 2": "playstation-2", "PS2": "playstation-2",
                "PlayStation 3": "playstation-3", "PS3": "playstation-3",
                "PlayStation 4": "playstation-4", "PS4": "playstation-4",
                "PlayStation 5": "playstation-5", "PS5": "playstation-5",
                "PSP": "psp", "PS Vita": "playstation-vita",
                "Xbox": "xbox", "Xbox 360": "xbox-360",
                "Xbox One": "xbox-one", "Xbox Series X": "xbox-series-x",
                "Sega Genesis": "sega-genesis", "Genesis": "sega-genesis",
                "Sega Saturn": "sega-saturn", "Saturn": "sega-saturn",
                "Sega Dreamcast": "sega-dreamcast", "Dreamcast": "sega-dreamcast",
                "Sega Master System": "sega-master-system",
                "Sega CD": "sega-cd", "Sega 32X": "sega-32x"
            }
            pc_platform = platform_map.get(platform, platform.lower().replace(" ", "-"))
            search_query = f"{item_name} {pc_platform}"
        elif category == "LEGO":
            search_query = f"lego {item_name}"
        elif category == "Comic Books":
            search_query = f"comic {item_name}"
        
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

def validate_inventory_item(data):
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
        raise ValueError(f"Invalid confidence: '{data['confidence']}' (must be HIGH/MEDIUM/LOW)")
    
    # Validate pricing_basis (with auto-fix for LLM indecision)
    valid_pricing = [
        'COMPLETE_IN_BOX', 'LOOSE_CART', 'LOOSE_DISC', 'NEW_SEALED',
        'LOOSE_ACCESSORY', 'CONSOLE_ONLY', 'COMPLETE_CONSOLE',
        'HANDHELD_ONLY', 'COMPLETE_HANDHELD', 'USED'
    ]
    
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
    
    # Validate item_name is not empty
    if not data['item_name'] or data['item_name'].strip() == '':
        raise ValueError("item_name cannot be empty")
    
    # Validate pricecharting_match_used if present (with auto-fix for hallucinated values)
    if data.get('pricecharting_match_used') is not None:
        match_value = data['pricecharting_match_used']
        
        if not isinstance(match_value, int):
            raise ValueError(f"pricecharting_match_used must be integer or null, got {type(match_value)}")
        
        # Auto-fix out-of-range values
        if match_value < 1 or match_value > PRICECHARTING_MAX_RESULTS:
            print(f"    ⚠️  Invalid pricecharting_match_used: {match_value} (must be 1-{PRICECHARTING_MAX_RESULTS})")
            print(f"    ⚠️  Setting to null - LLM hallucinated option number")
            data['pricecharting_match_used'] = None
            data['manual_review_recommended'] = True
            if not data.get('manual_review_reason'):
                data['manual_review_reason'] = "LLM provided invalid PriceCharting match - please verify pricing"
    
    return True

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
  "condition_notes": "Brief notes",
  "variant_notes": "Important variants, editions, regional differences",
  "personal_effect_eligible": true,
  "warnings": [],
  "pricecharting_match_used": 1-{PRICECHARTING_MAX_RESULTS} or null,
  "pricecharting_match_confidence": "HIGH/MEDIUM/LOW/NONE",
  "manual_review_recommended": false,
  "manual_review_reason": ""
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

CONDITION DEFAULTS (platform-based, you cannot see the actual scan):

**8-BIT/16-BIT CARTRIDGES (default: LOOSE_CART):**
- NES, SNES, Genesis, Master System, Game Boy/GBC/GBA, TurboGrafx-16, Atari
- Override only if search explicitly states "complete", "CIB", "sealed"

**DISC-BASED (default: COMPLETE_IN_BOX):**
- PlayStation, Xbox, GameCube, Sega CD/Saturn/Dreamcast, PC games
- Override only if search says "disc only" or "no case"

**MODERN CARTRIDGES (default: COMPLETE_IN_BOX):**
- DS, 3DS, Switch, PS Vita
- Override only if search explicitly indicates otherwise

**SEALED (NEW_SEALED):**
- Only if search consistently shows "factory sealed", "NIB", "unopened"

**ACCESSORIES:**
- LOOSE_ACCESSORY unless search mentions original packaging

**CONSOLES/HANDHELDS:**
- Use CONSOLE_ONLY, COMPLETE_CONSOLE, HANDHELD_ONLY, COMPLETE_HANDHELD based on descriptions

PRICECHARTING MATCHING:
- Match BOTH item name AND region
- Japanese cart → prefer Japanese listing
- US cart → prefer NTSC-U listing
- Set pricecharting_match_confidence: HIGH (clear match), MEDIUM (uncertain), LOW (questionable), NONE (no match)
- Use appropriate price: LOOSE_CART→loose_price, COMPLETE_IN_BOX→cib_price, NEW_SEALED→new_price
- If regional mismatch, set to null and warn

CATEGORIES:
Video Game Software, Video Game Console, Video Game Accessory, Handheld Game System, LEGO, Comic Books, Electronics, Collectibles

PERSONAL_EFFECT_ELIGIBLE:
- true: typical consumer items for personal use
- false: brand new sealed or luxury >$1000

MANUAL REVIEW:
- Flag if condition drastically affects value (10x+)
- Flag if regional variant uncertain
- Flag if conflicting Google results

Return ONLY valid JSON."""

    try:
        response = requests.post(
            OLLAMA_ENDPOINT,
            json={
                "model": LLM_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_ctx": 8192, "num_predict": 2048}
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
        
        # Parse JSON
        parsed = json.loads(response_text)
        
        # Validate schema
        try:
            validate_inventory_item(parsed)
        except ValueError as e:
            if VERBOSE_LOGGING:
                print(f"    ⚠️  Validation error: {e}")
                print(f"    Raw response: {response_text[:500]}")
            # Include item name in error for context
            item_hint = parsed.get('item_name', 'Unknown')
            raise Exception(f"Invalid LLM output for '{item_hint}': {e}")
        
        return parsed
        
    except json.JSONDecodeError as e:
        if VERBOSE_LOGGING:
            print(f"    ⚠️  JSON parse error: {e}")
            print(f"    Raw response: {response_text[:500]}")
        raise Exception(f"LLM returned invalid JSON: {e}")
    except Exception as e:
        raise Exception(f"LLM failed: {e}")

# ========================================
# ITEM PROCESSING
# ========================================

def process_item(image_path, tote_info, item_sequence):
    """Process single item scan"""
    print(f"\n  📦 Item #{item_sequence} in {tote_info['tote_id']}")
    
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
        base_filename = f"{item_name_safe}_{tote_info['tote_id_safe']}"
        new_filename = f"{base_filename}{image_path.suffix}"
        new_path = tote_dir / new_filename
        
        # Handle duplicate filenames by adding counter
        if new_path.exists():
            counter = 2
            while new_path.exists():
                new_filename = f"{base_filename}_{counter}{image_path.suffix}"
                new_path = tote_dir / new_filename
                counter += 1
            print(f"    ⚠️  Duplicate item name - added counter: _{counter-1}")
        
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