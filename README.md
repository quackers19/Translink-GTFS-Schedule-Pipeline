# Translink GTFS Schedule to SQLite

A tool to convert GTFS Schedule feeds (from Translink or any GTFS source) into a structured SQLite database. Supports optional filtering to keep only records relevant to specified stops.

## Features

- Converts GTFS `.txt` files (from zip or directory) to SQLite database
- Handles both local and remote GTFS feeds via HTTP(S)
- Optional database trimming to keep only records for specific stops and their connected routes
- GitHub Action for CI/CD workflows

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Command Line

#### Basic: Full Database Import

```bash
python parser.py --input-url /path/to/gtfs.zip --output gtfs.sqlite
```

#### Download from URL

```bash
python parser.py --input-url https://example.com/gtfs.zip --output gtfs.sqlite
```

#### Specify Custom Schema

```bash
python parser.py --input-url gtfs.zip --output gtfs.sqlite --schema /path/to/schema.sql
```

#### Trim Database to Specific Stops

Keep only records for transport that passes through specified stops, 
any stop id can be found on translinks website for a specific stop note this is not the number displayed on the physical stop sign:

```bash
python parser.py \
  --input-url gtfs.zip \
  --output gtfs.sqlite \
  --stop-ids "STOP001,152,000007"
```

You can also pass stop IDs as newline-separated values:

```bash
python parser.py \
  --input-url gtfs.zip \
  --output gtfs.sqlite \
  --stop-ids $'STOP001\nSTOP002\nSTOP003'
```

#### Prevent Overwriting Existing Database

```bash
python parser.py \
  --input-url gtfs.zip \
  --output gtfs.sqlite \
  --no-overwrite
```

### GitHub Action

#### Basic Workflow

```yaml
name: Build GTFS Database

on:
  workflow_dispatch:
  schedule:
    - cron: '0 2 * * 0'  # Weekly

jobs:
  build-db:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      
      - name: Build GTFS Database
        uses: quackers19/Translink-GTFS-Schedule-Pipeline@v1.0.0
        with:
          gtfs-url: 'https://example.com/translink-gtfs.zip'
          output: 'gtfs.db'
      
      - name: Upload Database
        uses: actions/upload-artifact@v7
        with:
          name: gtfs-database
          path: gtfs.db
```

#### Trim to Specific Stops

```yaml
name: Build Regional GTFS Database

on:
  workflow_dispatch:

jobs:
  build-regional:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      
      - name: Build GTFS (Region Only)
        uses: quackers19/Translink-GTFS-Schedule-Pipeline@v1.0.0

        with:
          gtfs-url: 'https://example.com/translink-gtfs.zip'
          output: 'gtfs.db'
          stop-ids: 'BT001,BT002,BT003,BT004,BT005'
      
      - name: Upload Database
        uses: actions/upload-artifact@v7
        with:
          name: gtfs
          path: gtfs.db
```

## Database Schema

The tool creates the following tables from standard GTFS files:

- `agency` – Transit agencies
- `routes` – Transit routes
- `stops` – Stop locations
- `trips` – Individual trips/journeys
- `stop_times` – Stop sequences for trips
- `calendar` – Service day schedules
- `calendar_dates` – Exception dates
- `shapes` – Route geometries
- `shape_points` – Individual shape coordinates
- `feed_info` – Feed metadata

## Stop Trimming Behavior

When using `--stop-ids`, the tool:

1. Finds all trips that visit any of the specified stops
2. Keeps all stops and stop_times on those trips (not just the specified stops)
3. Removes:
   - Trips that don't visit any specified stops
   - Routes with no remaining trips
   - Calendar entries not used by remaining trips
   - Shapes not used by remaining trips
   - Agencies with no remaining routes
4. Runs `VACUUM` to compact the database file

**Example:** If you specify stops `[A, B, C]` and a trip visits `[A, X, B, Y, C]`, all five stops (`A, B, C, X, Y`) are kept along with the full trip record.

