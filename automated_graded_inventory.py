#!/usr/bin/env python3
"""
AUTOMATED GRADED INVENTORY SCANNER
====================================

Live scanner for GRADED comic books and trading cards. Uses a two-pass LLM
process to read grades directly from slab images before identification.

DIFFERENCES FROM automated_inventory.py:
-----------------------------------------
This script adds a multimodal vision pass (Pass 1) that examines the scanned
image to extract the grade number and grading authority from the slab label,
then feeds that information into the text-only LLM (Pass 2) alongside the
Google Lens and PriceCharting data for final JSON output.

  Pass 1 (Vision):  Downscaled image → Multimodal LLM → grade + grading authority
  Pass 2 (Text):    Grade info + Google Lens + PriceCharting → Qwen 2.5 → JSON

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
   Terminal 4: ollama run deepseek-ocr  (or your VISION_MODEL)

USAGE:
------
python automated_graded_inventory.py

Then use Czur scanner:
1. Scan tote QR label → Creates tote directory, sets context
2. Scan graded items → Vision reads grade, then full analysis
3. Repeat for next tote

WORKFLOW:
---------
Scan: TOTE-001 QR code
  → Creates /organized/TOTE-001/
  → Sets current tote context

Scan: Graded item photo
  → Pass 1: Vision LLM reads grade + authority from slab image
  → Google Lens identifies item
  → PriceCharting lookup (if comic/trading card)
  → Pass 2: Text LLM synthesizes all data into structured JSON
  → Auto-cropped to remove black mat (if enabled)
  → Renamed: Amazing_Spider-Man_1_CGC_98_001_TOTE-001.jpg
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
import base64
import io
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

# Ollama Configuration (text-only LLM - Pass 2)
OLLAMA_ENDPOINT = os.getenv('OLLAMA_ENDPOINT', 'http://localhost:11434/api/generate')
LLM_MODEL = os.getenv('LLM_MODEL', 'qwen2.5:32b')
OLLAMA_TIMEOUT = int(os.getenv('OLLAMA_TIMEOUT', '120'))

# Vision Model Configuration (multimodal LLM - Pass 1)
VISION_MODEL = os.getenv('VISION_MODEL', 'deepseek-ocr')
VISION_TIMEOUT = int(os.getenv('VISION_TIMEOUT', '120'))
DOWNSCALE_DPI = int(os.getenv('DOWNSCALE_DPI', '72'))

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

def downscale_image_to_base64(image_path):
    """Downscale image based on DPI and return as base64 string for vision LLM.

    Reads the source DPI from image metadata and scales down to DOWNSCALE_DPI.
    For example, a 300 DPI scan downscaled to 72 DPI becomes ~24% of original size.
    Creates a temporary in-memory downscaled version - no temp files on disk.
    Returns base64-encoded JPEG string.
    """
    img = Image.open(image_path)
    width, height = img.size

    # Read source DPI from image metadata (default 300 if not embedded)
    source_dpi = 300
    dpi_info = img.info.get('dpi')
    if dpi_info:
        # dpi_info is typically a tuple (x_dpi, y_dpi); use the larger axis
        try:
            source_dpi = max(int(dpi_info[0]), int(dpi_info[1]))
        except (TypeError, IndexError, ValueError):
            pass

    # Only downscale if source DPI exceeds target
    if source_dpi > DOWNSCALE_DPI:
        scale = DOWNSCALE_DPI / source_dpi
        new_width = max(1, int(width * scale))
        new_height = max(1, int(height * scale))
        img = img.resize((new_width, new_height), Image.LANCZOS)
        if VERBOSE_LOGGING:
            print(f"    [Vision] Downscaled {width}x{height} @ {source_dpi}dpi → {new_width}x{new_height} @ {DOWNSCALE_DPI}dpi")
    else:
        if VERBOSE_LOGGING:
            print(f"    [Vision] No downscale needed ({source_dpi}dpi <= {DOWNSCALE_DPI}dpi target)")

    # Convert to RGB if necessary (handles RGBA, palette, etc.)
    if img.mode != 'RGB':
        img = img.convert('RGB')

    # Encode to JPEG in memory and convert to base64
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=85)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode('utf-8')

# ========================================
# PASS 1: VISION LLM - GRADE EXTRACTION
# ========================================

def extract_grade_from_image(image_path):
    """Pass 1: Send downscaled image to multimodal LLM to read grade info.

    Examines the grading slab/case label to extract:
    - grade: numerical grade (e.g., 9.8, 9.6, 8.0)
    - grading_authority: company name (e.g., CGC, PGX, CBCS, PSA, BGS, SGC)
    - certification_number: cert/serial number on the label (if visible)
    - label_color: color of the grade label (e.g., blue, gold, green)

    Returns a dict with extracted info, or empty values if not a graded item.
    """
    print(f"    👁️  Vision: Reading grade from slab...")

    image_b64 = downscale_image_to_base64(image_path)

    prompt = """You are examining a photograph of a collectible item that may be professionally graded (in a protective slab/case).

Look at this image carefully and extract the following information from the grading label/slab:

1. GRADE: The numerical grade on the label (e.g., 9.8, 9.6, 9.4, 9.2, 9.0, 8.5, 8.0, 7.5, 7.0, etc.)
2. GRADING AUTHORITY: The grading company name or logo visible on the slab. Common ones:
   - Comics: CGC (Certified Guaranty Company), CBCS (Comic Book Certification Service), PGX (Professional Grading Experts)
   - Trading Cards: PSA (Professional Sports Authenticator), BGS (Beckett Grading Services), SGC (Sportscard Guaranty Corporation), CGC (CGC Trading Cards)
3. CERTIFICATION NUMBER: The serial/certification number printed on the label (if visible)
4. LABEL COLOR: The color of the grading label (e.g., blue universal, gold signature, green qualified, purple restored for CGC; or equivalent for other companies)

Return ONLY valid JSON in this exact format:
{
  "grade": 9.8,
  "grading_authority": "CGC",
  "certification_number": "1234567890",
  "label_color": "blue universal"
}

RULES:
- If you can clearly read the grade number, return it as a float (e.g., 9.8, not "9.8")
- If you can identify the grading company, return its standard abbreviation (CGC, CBCS, PGX, PSA, BGS, SGC)
- If the item is NOT in a grading slab (raw/ungraded), return all null values:
  {"grade": null, "grading_authority": null, "certification_number": null, "label_color": null}
- If you can see a slab but cannot read a specific field, set that field to null
- Do NOT guess or infer grades - only report what you can actually read on the label
- Return ONLY the JSON object, no other text"""

    try:
        # Ollama multimodal API uses /api/generate with images array
        response = requests.post(
            OLLAMA_ENDPOINT,
            json={
                "model": VISION_MODEL,
                "prompt": prompt,
                "images": [image_b64],
                "stream": False,
                "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 512}
            },
            timeout=VISION_TIMEOUT
        )

        if not response.ok:
            print(f"    ⚠️  Vision model error: {response.status_code}")
            return {"grade": None, "grading_authority": None, "certification_number": None, "label_color": None}

        result = response.json()
        response_text = result.get('response', '').strip()

        if not response_text:
            print(f"    ⚠️  Vision: Empty response")
            return {"grade": None, "grading_authority": None, "certification_number": None, "label_color": None}

        # Strip markdown code blocks if present
        if '```json' in response_text:
            response_text = response_text.split('```json')[1].split('```')[0].strip()
        elif '```' in response_text:
            response_text = response_text.split('```')[1].split('```')[0].strip()

        parsed = json.loads(response_text)

        # Validate and normalize the response
        grade = parsed.get('grade')
        grading_authority = parsed.get('grading_authority')
        cert_number = parsed.get('certification_number')
        label_color = parsed.get('label_color')

        # Normalize grading authority to standard abbreviation
        if grading_authority and isinstance(grading_authority, str):
            authority_map = {
                "certified guaranty company": "CGC",
                "cgc": "CGC",
                "comic book certification service": "CBCS",
                "cbcs": "CBCS",
                "professional grading experts": "PGX",
                "pgx": "PGX",
                "professional sports authenticator": "PSA",
                "psa": "PSA",
                "beckett grading services": "BGS",
                "beckett": "BGS",
                "bgs": "BGS",
                "sportscard guaranty corporation": "SGC",
                "sportscard guaranty": "SGC",
                "sgc": "SGC",
            }
            grading_authority = authority_map.get(grading_authority.lower().strip(), grading_authority.upper().strip())

        # Validate grade is a reasonable number
        if grade is not None:
            try:
                grade = float(grade)
                if grade < 0.5 or grade > 10.0:
                    print(f"    ⚠️  Vision: Grade {grade} out of range (0.5-10.0), discarding")
                    grade = None
            except (ValueError, TypeError):
                print(f"    ⚠️  Vision: Non-numeric grade '{grade}', discarding")
                grade = None

        vision_result = {
            "grade": grade,
            "grading_authority": grading_authority,
            "certification_number": str(cert_number) if cert_number else None,
            "label_color": label_color
        }

        # Log what was found
        if grade is not None and grading_authority is not None:
            print(f"    👁️  Vision: {grading_authority} {grade}")
            if cert_number:
                print(f"    👁️  Vision: Cert #{cert_number}")
        elif grade is not None:
            print(f"    👁️  Vision: Grade {grade} (authority unclear)")
        elif grading_authority is not None:
            print(f"    👁️  Vision: {grading_authority} slab detected (grade unclear)")
        else:
            print(f"    👁️  Vision: No grading slab detected")

        return vision_result

    except json.JSONDecodeError as e:
        if VERBOSE_LOGGING:
            print(f"    ⚠️  Vision JSON parse error: {e}")
            print(f"    Vision raw response: {response_text[:500]}")
        return {"grade": None, "grading_authority": None, "certification_number": None, "label_color": None}
    except Exception as e:
        print(f"    ⚠️  Vision error: {e}")
        return {"grade": None, "grading_authority": None, "certification_number": None, "label_color": None}

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

    if not (is_game or is_lego or is_comic or is_card):
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
        'HANDHELD_ONLY', 'COMPLETE_HANDHELD', 'USED', 'GRADED_SLAB'
    ]

    pricing_basis = data['pricing_basis']

    if pricing_basis is None:
        data['pricing_basis'] = 'GRADED_SLAB'
        data['manual_review_recommended'] = True
        if not data.get('manual_review_reason'):
            data['manual_review_reason'] = "LLM could not determine condition - please verify"
        print(f"    ⚠️  LLM returned null pricing_basis, defaulting to GRADED_SLAB and flagging for review")
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
    if not data['item_name'] or not isinstance(data['item_name'], str) or data['item_name'].strip() == '':
        raise ValueError("item_name cannot be empty or null")

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

    # Validate certification_number if present
    if data.get('certification_number') is not None and not isinstance(data['certification_number'], str):
        data['certification_number'] = str(data['certification_number'])

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

    # Cross-validate vision grade vs LLM grade
    if data.get('vision_grade') is not None and data.get('grade') is not None:
        if abs(data['vision_grade'] - data['grade']) > 0.1:
            data['warnings'] = data.get('warnings', [])
            data['warnings'].append(
                f"Grade mismatch: Vision read {data['vision_grade']} but LLM determined {data['grade']}. "
                f"Vision reading is typically more reliable for slab labels."
            )
            data['manual_review_recommended'] = True
            if not data.get('manual_review_reason'):
                data['manual_review_reason'] = "Grade mismatch between vision and text analysis"
            print(f"    ⚠️  Grade mismatch: Vision={data['vision_grade']} vs LLM={data['grade']}")

    return True

# ========================================
# PASS 2: TEXT LLM ANALYSIS
# ========================================

def analyze_with_llm(search_results_text, vision_grade_info, pricecharting_results=None):
    """Pass 2: Text LLM synthesis of vision grade + Google + PriceCharting data"""

    context = search_results_text

    # Add vision grade info
    context += "\n=== VISION GRADE EXTRACTION (from slab image) ===\n"
    if vision_grade_info.get('grade') is not None or vision_grade_info.get('grading_authority') is not None:
        context += "A multimodal vision model examined the scanned image and extracted:\n"
        if vision_grade_info.get('grading_authority'):
            context += f"  Grading Authority: {vision_grade_info['grading_authority']}\n"
        if vision_grade_info.get('grade') is not None:
            context += f"  Grade: {vision_grade_info['grade']}\n"
        if vision_grade_info.get('certification_number'):
            context += f"  Certification Number: {vision_grade_info['certification_number']}\n"
        if vision_grade_info.get('label_color'):
            context += f"  Label Color: {vision_grade_info['label_color']}\n"
        context += "\nIMPORTANT: The vision model read these values directly from the slab label.\n"
        context += "Trust the vision-extracted grade and grading authority as the primary source.\n"
        context += "Only override if Google search results CLEARLY contradict the vision reading.\n"
    else:
        context += "The vision model did NOT detect a grading slab on this item.\n"
        context += "This may be a raw/ungraded item, or the slab label was not visible.\n"
        context += "If Google search results indicate this is a graded item, use that info.\n"
        context += "Otherwise, treat as ungraded.\n"

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

Analyze search results and vision grade data for this GRADED collectible. Return JSON:

{{
  "item_name": "Full title including key identifiers (e.g., 'Amazing Spider-Man #300')",
  "platform": "Gaming platform if applicable, null for comics/cards",
  "region": "NTSC-U/PAL/NTSC-J or null",
  "region_reasoning": "Why you determined this region from text indicators",
  "confidence": "HIGH/MEDIUM/LOW",
  "confidence_reason": "Brief explanation",
  "estimated_value_usd": 0.00,
  "value_range_min": 0.00,
  "value_range_max": 0.00,
  "price_source": "Which sources used",
  "pricing_basis": "GRADED_SLAB/COMPLETE_IN_BOX/LOOSE_CART/LOOSE_DISC/NEW_SEALED/USED",
  "category": "Comic Books or Trading Cards or other category",
  "condition_notes": "Brief notes about the slab/grade condition",
  "variant_notes": "Important variants, editions, printings, regional differences",
  "personal_effect_eligible": true,
  "warnings": [],
  "pricecharting_match_used": {f"1-{len(pricecharting_results)} or null" if pricecharting_results else "null"},
  "pricecharting_match_confidence": "HIGH/MEDIUM/LOW/NONE",
  "manual_review_recommended": false,
  "manual_review_reason": "",
  "issue_number": "Comic issue number or card number as string (e.g., '300', '4') or null",
  "year": "Publication or release year as integer (e.g., 1988, 2023) or null",
  "grade": {vision_grade_info.get('grade') if vision_grade_info.get('grade') is not None else "null or grade from search results"},
  "grader": {f'"{vision_grade_info["grading_authority"]}"' if vision_grade_info.get('grading_authority') else '"CGC/CBCS/PGX/PSA/BGS/SGC or null"'},
  "certification_number": {f'"{vision_grade_info["certification_number"]}"' if vision_grade_info.get('certification_number') else "null"},
  "label_color": {f'"{vision_grade_info["label_color"]}"' if vision_grade_info.get('label_color') else "null"}
}}

GRADED ITEM RULES:

1. GRADE AND GRADING AUTHORITY:
   - The vision model has already examined the slab image and extracted grade/authority.
   - TRUST the vision-extracted values as the primary source.
   - If vision found grade={vision_grade_info.get('grade')} and authority={vision_grade_info.get('grading_authority')}, USE those values.
   - Only override vision values if Google results CLEARLY show different info (e.g., the listing specifically states a different grade).
   - If vision returned null for both, check Google results for grade mentions.

2. PRICING FOR GRADED ITEMS:
   - Graded items are worth MORE than raw copies. The grade significantly affects value.
   - pricing_basis should be "GRADED_SLAB" for any professionally graded item.
   - For graded comics: Higher grades (9.8, 9.6) command significant premiums.
     Key grade tiers: 9.8 (Near Mint/Mint), 9.6 (Near Mint+), 9.4 (Near Mint), 9.2, 9.0, 8.5, 8.0, etc.
   - For graded cards: PSA 10 and BGS 10/9.5 are the premium grades.
     Key grade tiers: PSA 10 (Gem Mint), PSA 9 (Mint), BGS 9.5 (Gem Mint), BGS 10 (Pristine)
   - Use Google search results and PriceCharting data to estimate graded value.
   - If you see prices for different grades, interpolate for the specific grade.

3. GRADING COMPANIES:
   Comics: CGC (most common/valuable), CBCS, PGX (least premium)
   Cards: PSA (most common), BGS/Beckett (premium for 10s), SGC, CGC
   - CGC-graded items typically command the highest premiums
   - PGX-graded comics trade at a discount vs CGC
   - PSA 10 cards are highly sought after

4. CERTIFICATION NUMBER:
   - If the vision model read a certification number, include it
   - This allows verification on the grading company's website

5. LABEL COLOR (CGC specific):
   - Blue (Universal): Standard, unrestored grade
   - Gold (Signature Series): Witnessed signature
   - Green (Qualified): Minor defect noted
   - Purple (Restored): Professional restoration detected
   - Label color affects value - Blue/Gold are most desirable

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

PRICECHARTING MATCHING:
{"- " + str(len(pricecharting_results)) + " options are listed above. Select ONLY from those options (1-" + str(len(pricecharting_results)) + ") or null." if pricecharting_results else "- No PriceCharting data available. pricecharting_match_used MUST be null."}
- Match BOTH item name AND region
- Set pricecharting_match_confidence: HIGH (clear match), MEDIUM (uncertain), LOW (questionable), NONE (no match)
- Note: PriceCharting prices are for RAW copies. Graded copies are worth more.

CATEGORIES:
Video Game Software, Video Game Console, Video Game Accessory, Handheld Game System, LEGO, Comic Books, Trading Cards, Electronics, Collectibles

PERSONAL_EFFECT_ELIGIBLE:
- true: typical consumer items for personal use
- false: brand new sealed or luxury >$1000

MANUAL REVIEW:
- Flag if grade could not be confirmed from both vision and search results
- Flag if value estimate has high uncertainty (graded items can vary wildly)
- Flag if grading authority is unclear or disputed

COMIC BOOKS:
- item_name should include title and issue number (e.g., "Amazing Spider-Man #300")
- issue_number is the issue number as string
- year is publication year
- Include key issue status in variant_notes if applicable (first appearances, etc.)

TRADING CARDS:
- item_name should include card name AND set name (e.g., "Charizard Pokemon Base Set")
- issue_number is the card number within the set
- platform field should be null for trading cards
- Include parallel/variant info in variant_notes (e.g., "1st Edition", "Shadowless", "Holo")

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

        # Inject vision grade for cross-validation
        if vision_grade_info.get('grade') is not None:
            parsed['vision_grade'] = vision_grade_info['grade']
        if vision_grade_info.get('grading_authority') is not None:
            parsed['vision_grading_authority'] = vision_grade_info['grading_authority']

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
    """Process single graded item scan with two-pass LLM and retry on transient errors"""
    print(f"\n  📦 Item #{item_sequence} in {tote_info['tote_id']}")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Pass 1: Vision LLM reads grade from slab
            print(f"    🔍 Pass 1: Vision grade extraction...")
            vision_grade_info = extract_grade_from_image(image_path)

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

            # Pass 2: Text LLM analysis (grade info + Google + PriceCharting)
            print(f"    🤖 Pass 2: Analyzing (grade + search data)...")
            analysis = analyze_with_llm(formatted_results, vision_grade_info, pricecharting_results)

            # Auto-crop before organizing
            autocrop_image(image_path)

            # Build filename incorporating grade info
            item_name_safe = sanitize_filename(analysis['item_name'])
            grade_suffix = ""
            if analysis.get('grader') and analysis.get('grade') is not None:
                grade_str = str(analysis['grade']).replace('.', '')
                grade_suffix = f"_{analysis['grader']}_{grade_str}"

            # Organize file
            tote_dir = ORGANIZED_DIR / tote_info['tote_id_safe']
            tote_dir.mkdir(parents=True, exist_ok=True)

            new_filename = f"{item_name_safe}{grade_suffix}_{item_sequence:03d}_{tote_info['tote_id_safe']}{image_path.suffix}"
            new_path = tote_dir / new_filename

            shutil.move(image_path, new_path)

            # Summary output
            grade_display = ""
            if analysis.get('grader') and analysis.get('grade') is not None:
                grade_display = f" [{analysis['grader']} {analysis['grade']}]"
            print(f"    ✓ {analysis['item_name']}{grade_display} (${analysis['estimated_value_usd']:.2f})")
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
                "vision_grade_info": vision_grade_info,
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

class GradedInventoryScanner(FileSystemEventHandler):
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

        print("🚀 Graded Inventory Scanner Ready")
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
            f.write("tote_id,item_sequence,item_name,category,grade,grader,estimated_value_usd,confidence,manual_review,status\n")
            for item in self.inventory:
                if item['status'] == 'success':
                    ai = item['ai_analysis']
                    review = "YES" if ai.get('manual_review_recommended') else "NO"
                    grade = ai.get('grade', '')
                    grade_str = str(grade) if grade is not None else ''
                    grader = ai.get('grader', '') or ''
                    f.write(f'"{item["tote_id"]}",{item["item_sequence"]},"{item["item_name"]}","{ai.get("category","")}","{grade_str}","{grader}",{ai.get("estimated_value_usd",0)},"{ai.get("confidence","")}",{review},success\n')
                else:
                    f.write(f'"{item.get("tote_id","")}",{item.get("item_sequence",0)},"","","","",0,"","",failed\n')

# ========================================
# MAIN
# ========================================

def main():
    print("="*70)
    print("AUTOMATED GRADED INVENTORY SYSTEM")
    print("Two-Pass LLM: Vision Grade Extraction + Text Analysis")
    print("="*70)
    print(f"\nConfiguration:")
    print(f"  Scans: {SCAN_DIR}")
    print(f"  Output: {ORGANIZED_DIR}")
    print(f"  Vision Model (Pass 1): {VISION_MODEL}")
    print(f"  Text Model (Pass 2): {LLM_MODEL}")
    print(f"  Downscale DPI: {DOWNSCALE_DPI}")
    print(f"  PriceCharting: {'Enabled' if PRICECHARTING_API_KEY else 'Disabled'}")
    print(f"  Auto-crop: {'Enabled' if AUTOCROP_ENABLED else 'Disabled'}")
    if AUTOCROP_ENABLED:
        print(f"  Crop fuzz: {AUTOCROP_FUZZ}%")
    print(f"\nWorkflow:")
    print(f"  1. Vision LLM reads grade/authority from slab image")
    print(f"  2. Google Lens identifies the item")
    print(f"  3. PriceCharting lookup (if applicable)")
    print(f"  4. Text LLM synthesizes all data into structured JSON")
    print("\n" + "="*70 + "\n")

    input("Press Enter to start...")

    scanner = GradedInventoryScanner()
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
        graded_count = sum(1 for i in scanner.inventory
                          if i['status'] == 'success' and i['ai_analysis'].get('grade') is not None)

        print(f"\n{'='*70}")
        print("FINAL SUMMARY")
        print(f"{'='*70}")
        print(f"Items: {len(scanner.inventory)}")
        print(f"Successful: {successful}")
        print(f"Graded: {graded_count}")
        print(f"Total value: ${total_value:,.2f}")
        print(f"Inventory: {scanner.inventory_file}")
        print(f"{'='*70}\n")

if __name__ == "__main__":
    main()
