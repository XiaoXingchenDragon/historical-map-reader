# Historical Map Reader MVP

Local EPUB/PDF reader MVP for history books. The backend parses EPUB chapters or text-layer PDF pages, extracts place mentions, resolves them through a local gazetteer, and stores everything in SQLite. The React + Next.js frontend highlights places in the text and links them with a Leaflet map.

## Quick Start From GitHub

```powershell
git clone <repo-url>
cd <repo-folder>
```

Start the backend:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Optional, improves English place extraction:
python -m spacy download en_core_web_sm
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Start the frontend in another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`, upload an EPUB or text-layer PDF, and click upload/process.

The repository does not include local uploaded books, SQLite runtime databases, raw GeoNames zip downloads, or secret `.env` files. If you want DeepSeek candidate search, copy `backend/.env.example` to `backend/.env` and fill in your own API key.

## What Works

- Upload EPUB/PDF files to `backend/data/books/`.
- Store books, chapters, paragraphs, places, place mentions, manual corrections, and place aliases in SQLite.
- Parse EPUB text with ebooklib and BeautifulSoup, and text-layer PDF files with PyMuPDF.
- Match local gazetteer aliases with long-name-first matching, which is useful for Chinese names without spaces.
- Seed specific historical places such as Gallipoli, Dardanelles, Hellespont, Canakkale, Bosporus, Marmara, Venice, Genoa, Alexandria, and Constantinople.
- Optionally use spaCy `en_core_web_sm` for English GPE/LOC extraction.
- Highlight places in the current chapter.
- Dynamic de-duplication: the first mention of a place uses the primary highlight color; repeated mentions stay highlighted with a lighter color. Map markers remain de-duplicated.
- Click a highlighted place to fly the map to its marker.
- Click a marker to scroll the reader to the matching paragraph.
- Manually correct a mention's coordinates and save the raw text as an alias for future processing.
- Click an unresolved place candidate to search it with DeepSeek or delete that candidate name from the whole book.

## What Does Not Work Yet

- No OCR for scanned image-only PDFs.
- No accounts, collaboration, or cloud sync.
- No complex Chinese NER yet. Chinese support currently comes from aliases in the local gazetteer.
- No historical layers, timeline, route inference, or heavy GIS service.
- The frontend renders backend-parsed paragraphs, not full epub.js layout restoration.

## Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m spacy download en_core_web_sm
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The spaCy model is recommended but optional. Without it, the backend still runs and uses the local gazetteer.

## Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. The default API base is `http://localhost:8000`.

```powershell
$env:NEXT_PUBLIC_API_BASE="http://localhost:8000"
npm run dev
```

## EPUB/PDF Test Flow

1. Start the backend.
2. Start the frontend.
3. Open `http://localhost:3000`.
4. Upload an `.epub` or text-layer `.pdf` file and click upload/process.
5. Open a book chapter.
6. The reader highlights matched places. The first mention uses the primary color, and repeated mentions use a lighter color.
7. Click a highlight to move the map.
8. Click a marker to scroll to the paragraph.
9. If coordinates are wrong, edit name, latitude, and longitude in the correction panel. Keep "save as alias" enabled to make the raw mention reusable.
10. If a grey dashed candidate looks like a real place, click it and choose "Search". The backend sends the book title and candidate sentence to DeepSeek, expands context only when needed, then prefers a local gazetteer match before falling back to LLM coordinates.
11. If a grey dashed candidate is noise, click it and choose "Delete" to hide that candidate name across the whole book.
12. After editing the gazetteer or saving new aliases, click reprocess on the book page to rebuild mentions for the uploaded EPUB/PDF.

## Extending Specific Historical Places

Edit `backend/data/gazetteer/places.json`. It is intentionally ASCII-only JSON with `\uXXXX` escaped Chinese aliases so it remains stable across Windows terminals and editors.

Example entry:

```json
{
  "canonical_name": "Dardanelles",
  "lat": 40.214,
  "lng": 26.426,
  "source": "seed",
  "aliases": ["Hellespont", "\u8fbe\u8fbe\u5c3c\u5c14\u6d77\u5ce1", "\u8d6b\u52d2\u65af\u6ec2"],
  "category": "strait",
  "period": "Ancient/Ottoman/Modern",
  "region": "Eastern Mediterranean",
  "context_keywords": ["Gallipoli", "Troy", "Aegean", "Marmara"],
  "note": "Strait connecting the Aegean Sea and the Sea of Marmara"
}
```

When the backend starts, the gazetteer is synced into `places` and `place_aliases`. Matching is long-name-first, so a longer alias such as Dardanelles Strait is preferred over a shorter partial alias.

## GeoNames Import

GeoNames import is offline. The app never downloads GeoNames at runtime or during backend startup.

Download these files manually:

- `https://download.geonames.org/export/dump/cities15000.zip`
- `https://download.geonames.org/export/dump/alternateNamesV2.zip`

Place them here:

```text
backend/data/imports/geonames/cities15000.zip
backend/data/imports/geonames/alternateNamesV2.zip
```

Run the importer:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python scripts\import_geonames.py
```

The script generates:

```text
backend/data/gazetteer/geonames_cities15000_zh_en.json
```

The generated records include only `cities15000` entries that have at least one Chinese alternate name. Alternate names are limited to `en`, `zh`, `zh-CN`, `zh-Hans`, `zh-Hant`, `zh-TW`, and `zh-HK`. Aliases are deduplicated and records are sorted by population descending.

For broader global coverage beyond cities, download:

- `https://download.geonames.org/export/dump/allCountries.zip`
- `https://download.geonames.org/export/dump/alternateNamesV2.zip`

Place them here:

```text
backend/data/imports/geonames/allCountries.zip
backend/data/imports/geonames/alternateNamesV2.zip
```

Then run:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python scripts\import_geonames_global.py
```

The global importer generates:

```text
backend/data/gazetteer/geonames_global_zh_en.json
```

It keeps selected GeoNames feature classes for countries, administrative regions, cities/towns, rivers, lakes, islands, mountains, regions, parks, archaeological sites, and ruins. Countries and first-level administrative regions are kept even when Chinese aliases are missing; other records are kept when they have Chinese aliases or meet the script's population threshold.

Restart the backend after generating the file:

```powershell
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The backend loads, in order:

- `backend/data/gazetteer/geonames_global_zh_en.json`
- `backend/data/gazetteer/geonames_cities15000_zh_en.json`
- `backend/data/gazetteer/places.json`
- `backend/data/gazetteer/historical_aliases.json`

GeoNames is a modern place-name database. Historical names still need the seed gazetteer and `historical_aliases.json`. If the same alias maps to multiple places, this MVP keeps the higher-population GeoNames record and does not yet do contextual disambiguation.

## Candidate Filtering

Unresolved candidates are deliberately conservative:

- candidates containing `\u7684`, `\u8fd9`, or `\u4e2a` are dropped
- generic sea words such as open sea, nearshore, coast, harbor, and fortress are dropped unless they are formal gazetteer matches
- phrases shaped like `xx le xx` are cleaned by keeping only the part after `le`; this turns text like "fleet burned Ischia Island" into the candidate "Ischia Island"
- a candidate must appear at least twice in the whole book
- deleted candidates are cached in SQLite per book and no longer shown as unresolved candidates
- suffix candidates include islands, coasts, peninsulas, straits, kingdoms, empires, ports, regions, lagoons, towns, rivers, cities, and provinces
- text shaped like `Chinese name (English Name)` or `Chinese name（English Name）` is resolved through the English alias when possible; if the English name matches a local place and the Chinese name does not, the Chinese name is highlighted and saved as a `paired_parenthetical` alias

## DeepSeek Candidate Search

Candidate search is optional. It requires a local environment variable, either in the shell or in `backend/.env`:

```text
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

The app does not call DeepSeek during startup or normal chapter loading. It only calls DeepSeek when you click "Search" on an unresolved candidate. The first request sends the book title, candidate, and containing sentence. If the model says the sentence is ambiguous or needs more context, the backend sends the full paragraph plus a small set of other sentences containing that candidate.

The model should return a canonical place name, aliases, confidence, and optional coordinates. The backend first tries to match the returned name/aliases against the local gazetteer and GeoNames aliases. If no local match exists, it can create an `llm_suggested` place from returned coordinates. This is an MVP workflow; it does not yet do advanced historical disambiguation.

## API

- `POST /api/books/upload`
- `POST /api/books/{book_id}/process`
- `GET /api/books`
- `GET /api/books/{book_id}/chapters`
- `GET /api/chapters/{chapter_id}`
- `GET /api/books/{book_id}/places`
- `POST /api/mentions/{mention_id}/correct`
- `POST /api/place-candidates/search`
- `POST /api/place-candidates/ignore`

## Next Steps

- Add a gazetteer import script for larger CSV/JSON datasets.
- Add lightweight context scoring for ambiguous places.
- Add an alias management page.
- Add a better Chinese NER layer later, after the gazetteer workflow is solid.
- Add a review queue for LLM-suggested places before committing them to the gazetteer.
- Map parsed paragraphs to epub.js CFI positions for a more faithful reader.
