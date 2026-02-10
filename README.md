# Automated Image-Recognition Based Inventory System

Automated inventory cataloging system for large media collections. Uses Google Lens image recognition, local LLM analysis, and PriceCharting API integration to catalog thousands of items with automated valuation and customs documentation.

## Overview

I built this because I'm planning to move internationally with a large collection of video games, LEGO sets, comics, and other collectibles. I'm making this public because it has a ton of uses outside of mine and I hope others find this useful.  This system automates the tedious process of creating detailed inventories for customs, insurance, and tracking. It combines:

- **Google Lens** visual identification via SerpAPI
- **Local LLM** (Qwen 2.5:32b) for intelligent data synthesis and regional variant detection
- **PriceCharting API** for authoritative video game, LEGO, comic book, and trading card pricing
- **QR-coded tote tracking** for physical organization
- **Automated file organization** with sequence-numbered filenames
- **Image auto-cropping** to remove scanner backgrounds

Perfect for collectors, expats, or anyone needing professional inventory documentation for international customs, insurance claims, or estate planning.

## Features

### Core Functionality

- ✅ **Live scanning workflow** - Watch directory for new book scanner images
- ✅ **Batch processing** - Process manually photographed large items
- ✅ **QR code tote tracking** - Organize items by physical storage container
- ✅ **Intelligent item identification** - Google Lens + LLM synthesis
- ✅ **Regional variant detection** - *Mostly* Distinguishes NTSC-J, NTSC-U, PAL versions
- ✅ **Platform-based condition defaults** - Generally acceptable assumptions (cartridge vs disc systems) given that newer systems tend to have plastic boxes which folks tend to keep vs cardboard ones of the 8/16-bit generation.
- ✅ **Duplicate handling** - Sequence numbers in filenames uniquely identify each item, even duplicates
- ✅ **Validation & auto-correction** - Catches and fixes LLM output errors
- ✅ **Manual review flagging** - Highlights uncertain identifications

### Data Management

- 📊 **Dual output formats** - Detailed JSON + spreadsheet CSV
- 💰 **Valuation tracking** - Per-item and total collection value
- 🏷️ **Zebra label generation** - QR-coded tote labels (ZPL format)
- 🔐 **Security seal tracking** - Associate numbered seals to containers
- 🗑️ **Item removal utility** - Clean deletion with automatic backup
- 🔄 **PriceCharting updater** - Batch update pricing data periodically. Requires a subscription which is $50 a month (woof). 

### Output Files

- `inventory.json` - Complete detailed records with AI analysis
- `inventory.csv` - Simplified spreadsheet summary
- `/TOTE-XXX/ItemName_001_TOTE-XXX.jpg` - Organized, cropped, sequence-numbered images by container
- `seal_tracking.json` - Physical security seal associations

## Use Cases

### International Customs Documentation

- Detailed item-by-item inventory with valuations
- Photos of each item for verification
- Exportable to customs-required formats
- Built-in personal effects eligibility flagging based on product release date

### Insurance Claims

- Complete photographic evidence
- Current market valuations
- Easily updatable pricing data
- Professional documentation format

### Estate Planning

- Comprehensive collection catalog
- Current fair market values (Pricecharting only, the FMV data from Google is hot garbage)
- Organized by storage location
- Shareable CSV format

## System Architecture

### Live Scanning Workflow

```
Czur Scanner → QR Code Detection → Google Lens → PriceCharting (optional) → 
LLM Analysis → Auto-Crop → Rename → Organize → Save to Inventory
```

### Components

**Scripts:**

1. `automated_inventory.py` - Live automated scanning
2. `batch_inventory.py` - Batch process manual photos
3. `generate_labels.py` - Create QR-coded tote labels
4. `manage_seals.py` - Track security seals
5. `update_pricecharting.py` - Batch pricing updates
6. `remove_item.py` - Item removal with backup

**Data Flow:**

- Scan images → Identify with Google Lens → Optionally query PriceCharting → LLM synthesizes results (I use Qwen 2.5 32B) → Validates output → Saves to JSON/CSV → Organizes files

## Prerequisites

### Hardware

- **Book scanner** (e.g., Czur) or camera for photographing items
- **Black mat** for consistent backgrounds (optional, enables auto-cropping)
- **Zebra label printer** (optional, for QR labels)

### Software

- **Python 3.8+**
- **Ollama** with a capable model (recommended: qwen2.5:32b)
- **ImageMagick** (for auto-cropping)
- **ngrok** (free or paid, for exposing local images to Google Lens)

### API Keys

- **SerpAPI** - Google Lens image search ($50/month for 5,000 searches)
- **PriceCharting** - Optional ($50/month for 10,000 requests)

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/prosolis/Ditto.git
cd Ditto
```

### 2. Install Python Dependencies

```bash
apt install python3-requests python3-dotenv python3-watchdog python3-pyzbar python3-willow
```

### 3. Install System Dependencies

```bash
# ImageMagick (for auto-cropping)
sudo apt install imagemagick

# Ollama (for LLM analysis)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:32b
```

### 4. Install ngrok

```bash
# Download from https://ngrok.com/download
# Or via package manager:
brew install ngrok  # macOS
# Or snap install ngrok on Linux
```

### 5. Configure Environment

```bash
# Copy example config
cp .env.example .env

# Edit with your settings
nano .env
```

Required configuration in `.env`:

```env
SERPAPI_KEY=your_serpapi_key_here
NGROK_URL=https://your-url.ngrok-free.app
SCAN_DIR=/path/to/scanner/output
ORGANIZED_DIR=/path/to/organized/inventory
```

See `.env.example` for all available options.

## Quick Start

### Generate Tote Labels (One-Time Setup)

```bash
# Generate 50 QR-coded labels
python generate_labels.py 50

# Print labels to Zebra printer
cat zpl_labels/print_all.zpl | lp -d YourZebraPrinter
```

### Start Live Scanning

```bash
# Terminal 1: Start HTTP server
cd <parent of SCAN_DIR>
python3 -m http.server 8000

# Terminal 2: Start ngrok tunnel
ngrok http 8000
# Copy the https URL to your .env file

# Terminal 3: Start Ollama
ollama run qwen2.5:32b

# Terminal 4: Start inventory scanner
python automated_inventory.py
```

### Scanning Workflow

1. **Scan tote QR label** - Sets context for following items
2. **Scan items** - Use foot pedal for rapid scanning
3. **System automatically:**
   - Identifies each item
   - Queries PriceCharting (if enabled)
   - Synthesizes data with LLM
   - Crops and renames image
   - Moves to organized folder
   - Appends to inventory

### Batch Process Large Items

```bash
# For items too large for book scanner
mkdir /photos/TOTE-005
# Take photos, copy to folder

python batch_inventory.py /photos/TOTE-005
```

## Configuration

### .env File Options

**API Keys:**

```env
SERPAPI_KEY=required
PRICECHARTING_API_KEY=optional
```

**Paths:**

```env
SCAN_DIR=/path/to/scanner/output
ORGANIZED_DIR=/path/to/inventory
INVENTORY_JSON=organized/inventory.json    # Override if stored elsewhere
INVENTORY_CSV=organized/inventory.csv      # Override if stored elsewhere
BACKUP_DIR=organized/backups               # Override if stored elsewhere
```

**Processing:**

```env
AUTOCROP_ENABLED=true          # Auto-crop images
AUTOCROP_FUZZ=10               # ImageMagick fuzz tolerance
PRICECHARTING_MAX_RESULTS=5    # PriceCharting options per item
```

**LLM:**

```env
LLM_MODEL=qwen2.5:32b
OLLAMA_TIMEOUT=120
```

See `.env.example` for complete documentation.

## Output Format

### inventory.json

Complete detailed record per item:

```json
{
  "timestamp": "2024-02-07T19:03:37.607670",
  "tote_id": "TOTE-001",
  "item_sequence": 42,
  "item_name": "Super Metroid",
  "image_file": "Super_Metroid_042_TOTE-001.jpg",
  "ai_analysis": {
    "item_name": "Super Metroid",
    "platform": "SNES",
    "region": "NTSC-U",
    "confidence": "HIGH",
    "estimated_value_usd": 75.00,
    "pricing_basis": "LOOSE_CART",
    "category": "Video Game Software",
    "pricecharting_match_confidence": "HIGH"
  },
  "pricecharting_data": [...],
  "status": "success"
}
```

### inventory.csv

Simplified spreadsheet:

```csv
tote_id,item_sequence,item_name,category,estimated_value_usd,confidence,manual_review,status
TOTE-001,42,Super Metroid,Video Game Software,75.00,HIGH,NO,success
```

## Advanced Features

### Regional Variant Detection

The system intelligently detects regional variants from search results:

- **NTSC-J** (Japan): Japanese text indicators, Super Famicom, PC Engine
- **NTSC-U** (USA/Canada): ESRB ratings, English text, SNES, Genesis
- **PAL** (Europe): PEGI ratings, multi-language, Mega Drive

Matches PriceCharting listings to the correct regional variant for accurate pricing.

### Platform-Based Condition Defaults

Smart assumptions based on gaming platform:

- **8/16-bit cartridges** (NES, SNES, Genesis, Master System, Game Boy/GBC/GBA, TurboGrafx-16, Atari, Neo Geo, Neo Geo Pocket/Color, WonderSwan/Color, Virtual Boy, Game Gear) → Default: LOOSE_CART
- **Disc-based systems** (PlayStation, Xbox, GameCube, Saturn, Dreamcast, Sega CD, 3DO, CDi, PC Engine CD) → Default: COMPLETE_IN_BOX
- **Modern cartridges** (DS, 3DS, Switch, PS Vita) → Default: COMPLETE_IN_BOX

Overrides defaults only when search results explicitly indicate different condition.

### Validation & Auto-Correction

Catches common LLM errors:

- ✅ Swapped min/max value ranges → Auto-fixes
- ✅ Multiple pricing_basis values → Takes first, flags for review
- ✅ Hallucinated PriceCharting option numbers → Sets to null, flags
- ✅ Invalid enums → Rejects with clear error
- ✅ Missing required fields → Rejects

### Manual Review Flagging

Automatically flags items needing human verification:

- Condition drastically affects value (10x+ difference)
- LLM uncertain about regional variant
- Conflicting Google search results
- PriceCharting match questionable

## Utilities

### Update PriceCharting Prices

Run periodically to refresh valuations:

```bash
# Update all items
python update_pricecharting.py

# Dry run (preview changes)
python update_pricecharting.py --dry-run

# Only update items without PriceCharting data
python update_pricecharting.py --new-only

# Only update specific categories
python update_pricecharting.py --categories "Video Game Software" "LEGO"
```

### Remove Erroneous Entries

```bash
# Sequence number is in the filename: ItemName_054_TOTE-002.jpg → sequence 54
python remove_item.py TOTE-002 54
# Prompts for confirmation
# Creates backup before removal
# Regenerates CSV

# Remove all failed scan entries at once
python remove_item.py --purge-failed
# Lists each failed entry with tote, sequence, and error
# Prompts for confirmation before removing
# Original images remain in SCAN_DIR for re-scanning
```

### Manage Security Seals

```bash
# View all seal assignments
python manage_seals.py view

# Assign seal to tote
python manage_seals.py assign TOTE-001 AB123456

# Bulk assignment mode
python manage_seals.py bulk
```

### Batch Crop Existing Images

```bash
# Crop all already-processed images
find organized/TOTE-* -type f \( -iname "*.jpg" -o -iname "*.png" \) -exec convert {} -fuzz 10% -trim +repage {} \;
```

## Cost Breakdown

### One-Time Costs

- Czur book scanner: ~$100-300 (or use existing camera)
- Zebra label printer: ~$200-400 (optional)

### Recurring Costs (During Active Scanning)

- **SerpAPI**: $50/month (5,000 searches) or $0 (100 free searches/month)
- **PriceCharting**: $50/month (10,000 requests) - optional, can run separately
- **ngrok Personal** (optional): $8/month for static domain
- **Total**: $50-108/month while actively scanning

### Cost Optimization

- Scan everything with just Google Lens first ($50/month)
- Run PriceCharting updates later in batch ($50 one-time)
- Use free ngrok tier (requires URL update each session)
- Cancel subscriptions between scanning sessions

**Example: 2,000 items scanned over 1 month = ~$100 total**

## Supported Platforms & Categories

### Video Game Platforms

**Nintendo:** NES, Famicom, SNES, Super Famicom, N64, GameCube, Wii, Wii U, Switch, Game Boy, Game Boy Color, Game Boy Advance, Nintendo DS, Nintendo 3DS, Virtual Boy

**PlayStation:** PS1, PS2, PS3, PS4, PS5, PSP, PS Vita

**Xbox:** Xbox, Xbox 360, Xbox One, Xbox Series X

**Sega:** Master System, Genesis/Mega Drive, Game Gear, Saturn, Dreamcast, Sega CD, Sega 32X

**SNK:** Neo Geo AES, Neo Geo MVS, Neo Geo Pocket, Neo Geo Pocket Color

**Other:** TurboGrafx-16/PC Engine, WonderSwan, WonderSwan Color, 3DO, CDi, Atari (2600, 7800, Jaguar, Lynx)

### Categories

- Video Game Software
- Video Game Consoles
- Video Game Accessories
- Handheld Game Systems
- LEGO Sets
- Comic Books
- Trading Cards
- Electronics
- Collectibles

## Troubleshooting

### "SERPAPI_KEY not found"

Ensure `.env` file exists and contains your SerpAPI key.

### "NGROK_URL not configured"

Update `.env` with your actual ngrok tunnel URL from `ngrok http 8000`.

### Images not cropping

Check ImageMagick is installed: `convert --version`

### LLM timeouts

Increase `OLLAMA_TIMEOUT` in `.env` or use faster model.

### Sequence number gaps after removing items

This is expected. Sequence numbers are immutable IDs baked into filenames (`ItemName_003_TOTE-001.jpg`). Gaps after removal are harmless — the scanner uses the highest existing sequence to determine the next number.

### PriceCharting not finding items

Try adjusting `PRICECHARTING_MAX_RESULTS` to 10 for more options.

## Customs Documentation Notes

The system generates inventory suitable for international customs:

- Item-by-item listing with photos
- Fair market valuations in USD
- Personal effects eligibility flags
- Exportable to spreadsheet formats

**For customs submission:**

1. Export `inventory.csv` to Excel
2. Group similar items if permitted by destination country
3. Translate to destination language if required
4. Have certified by consulate if required
5. Include high-value items individually

**Consult destination country's customs requirements for specific format.**

## Development

### Project Structure

```
.
├── automated_inventory.py      # Live scanning
├── batch_inventory.py         # Batch processing
├── generate_labels.py         # QR label generation
├── manage_seals.py           # Seal tracking
├── update_pricecharting.py   # Price updates
├── remove_item.py            # Item removal
├── .env.example              # Config template
├── README.md                 # Documentation
└── organized/                 # Output directory (ORGANIZED_DIR)
    ├── inventory.json         # Complete inventory data
    ├── inventory.csv          # Spreadsheet summary
    ├── backups/               # Pre-modification backups
    └── TOTE-XXX/              # Per-tote image directories
        └── ItemName_001_TOTE-XXX.jpg
```

### Contributing

Contributions welcome! Please open an issue first to discuss changes.

### Testing

Test with small batches first:

1. Generate 5 test labels
2. Scan 10-20 items
3. Verify accuracy before bulk processing

## License

MIT License - See LICENSE file for details

## Acknowledgments

- **SerpAPI** for Google Lens API access
- **PriceCharting** for video game pricing data
- **Ollama** for local LLM infrastructure
- **Anthropic** for Claude AI assistance in development

## Support

For issues or questions:

- Open a GitHub issue
- Check `.env.example` for configuration help
- Review troubleshooting section above

---

**Note:** This system uses AI for automated identification. Always verify high-value items manually. The developers are not responsible for customs compliance - consult your destination country's requirements.
