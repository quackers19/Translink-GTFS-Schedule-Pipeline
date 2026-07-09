import argparse
import csv
import os
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from urllib.parse import urlparse
from urllib.request import urlopen

DEFAULT_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
DEFAULT_OUTPUT_FILENAME = "gtfs.sqlite"


def load_schema_sql(schema_path):
    """Read the CREATE TABLE / CREATE INDEX statements from schema.sql."""
    if not os.path.isfile(schema_path):
        raise FileNotFoundError(
            f"Schema file not found: {schema_path}\n"
            f"(Pass --schema to point at a different schema.sql file.)"
        )
    with open(schema_path, "r", encoding="utf-8") as f:
        return f.read()


# Column type families, used to coerce CSV strings -> Python values.
COLUMN_TYPES = {
    "agency": {},
    "feed_info": {},
    "stops": {
        "stop_lat": "REAL",
        "stop_lon": "REAL",
        "location_type": "INTEGER",
        "wheelchair_boarding": "INTEGER",
    },
    "routes": {
        "route_type": "INTEGER",
        "route_sort_order": "INTEGER",
        "continuous_pickup": "INTEGER",
        "continuous_drop_off": "INTEGER",
    },
    "calendar": {
        "monday": "INTEGER",
        "tuesday": "INTEGER",
        "wednesday": "INTEGER",
        "thursday": "INTEGER",
        "friday": "INTEGER",
        "saturday": "INTEGER",
        "sunday": "INTEGER",
    },
    "calendar_dates": {
        "exception_type": "INTEGER",
    },
    "trips": {
        "direction_id": "INTEGER",
        "wheelchair_accessible": "INTEGER",
        "bikes_allowed": "INTEGER",
    },
    "stop_times": {
        "stop_sequence": "INTEGER",
        "pickup_type": "INTEGER",
        "drop_off_type": "INTEGER",
        "continuous_pickup": "INTEGER",
        "continuous_drop_off": "INTEGER",
        "shape_dist_traveled": "REAL",
        "timepoint": "INTEGER",
    },
    "shapes": {
        "shape_id": None,
        "shape_pt_lat": "REAL",
        "shape_pt_lon": "REAL",
        "shape_pt_sequence": "INTEGER",
        "shape_dist_traveled": "REAL",
    },
    "shape_points": {
        "shape_pt_lat": "REAL",
        "shape_pt_lon": "REAL",
        "shape_pt_sequence": "INTEGER",
        "shape_dist_traveled": "REAL",
    },
}

# Maps GTFS filename -> table name, and defines load order so that
# foreign-key referenced tables are populated before their dependents.
FILE_TO_TABLE = [
    ("agency.txt", "agency"),
    ("feed_info.txt", "feed_info"),
    ("stops.txt", "stops"),
    ("routes.txt", "routes"),
    ("calendar.txt", "calendar"),
    ("calendar_dates.txt", "calendar_dates"),
    ("shapes.txt", "shapes"),
    ("trips.txt", "trips"),
    ("stop_times.txt", "stop_times"),
]


def coerce_value(raw_value, type_family):
    """Convert a raw CSV string into an appropriate Python value for sqlite3."""
    if raw_value is None:
        return None
    value = raw_value.strip()
    if value == "":
        return None
    if type_family == "INTEGER":
        try:
            return int(value)
        except ValueError:
            # Some feeds encode integer-like fields as floats (e.g. "1.0")
            return int(float(value))
    if type_family == "REAL":
        return float(value)
    return value


@contextmanager
def resolve_gtfs_dir(input_path):
    """
    Yields a directory path containing the GTFS .txt files, whether the
    input is a .zip archive or already a directory.
    """
    if os.path.isdir(input_path):
        yield input_path
        return

    if not zipfile.is_zipfile(input_path):
        raise ValueError(f"Input path is neither a directory nor a valid zip file: {input_path}")

    with tempfile.TemporaryDirectory(prefix="gtfs_extract_") as tmp_dir:
        with zipfile.ZipFile(input_path) as zf:
            zf.extractall(tmp_dir)

        # Some GTFS zips nest the .txt files inside a single subfolder
        # rather than at the archive root. Detect that case and point
        # to the folder that actually contains the .txt files.
        if not any(f.endswith(".txt") for f in os.listdir(tmp_dir)):
            for root, _dirs, files in os.walk(tmp_dir):
                if any(f.endswith(".txt") for f in files):
                    yield root
                    return
            raise ValueError("No GTFS .txt files found inside the provided zip archive.")
        else:
            yield tmp_dir


def is_http_url(value):
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


@contextmanager
def resolve_input_path(input_ref):
    """Yield a local filesystem path for either a local input or an HTTP(S) URL."""
    if not is_http_url(input_ref):
        yield input_ref
        return

    with tempfile.NamedTemporaryFile(prefix="gtfs_download_", suffix=".zip", delete=False) as tmp_file:
        tmp_path = tmp_file.name

    try:
        print(f"Downloading GTFS feed from URL: {input_ref}")
        with urlopen(input_ref) as response, open(tmp_path, "wb") as out_file:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out_file.write(chunk)

        yield tmp_path
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def load_file_into_table(conn, gtfs_dir, filename, table_name):
    file_path = os.path.join(gtfs_dir, filename)
    if not os.path.isfile(file_path):
        print(f"  [skip] {filename} not found in input, skipping table '{table_name}'.")
        return 0
    #need to handle seperatly as foreign key validation fails 
    # due to shape_id being non unique despite being a compesite key
    if table_name == "shapes":
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                print(f"  [skip] {filename} is empty.")
                return 0

            header = [h.strip() for h in header]
            points_placeholders = ", ".join("?" for _ in header)
            points_columns_sql = ", ".join(f'"{h}"' for h in header)
            points_insert_sql = f'INSERT INTO shape_points ({points_columns_sql}) VALUES ({points_placeholders})'

            distinct_shape_ids = set()
            point_rows_to_insert = []
            for row in reader:
                if not row or all(cell.strip() == "" for cell in row):
                    continue

                if len(row) < len(header):
                    row = row + [""] * (len(header) - len(row))
                elif len(row) > len(header):
                    row = row[: len(header)]

                distinct_shape_ids.add(row[header.index("shape_id")].strip())
                coerced = [
                    coerce_value(value, COLUMN_TYPES["shape_points"].get(col))
                    for col, value in zip(header, row)
                ]
                point_rows_to_insert.append(coerced)

            inserted_shape_ids = 0
            if distinct_shape_ids:
                conn.executemany(
                    "INSERT OR IGNORE INTO shapes (shape_id) VALUES (?)",
                    ((shape_id,) for shape_id in distinct_shape_ids if shape_id),
                )
                inserted_shape_ids = len(distinct_shape_ids)

            if point_rows_to_insert:
                conn.executemany(points_insert_sql, point_rows_to_insert)

            print(
                f"  [ok]   {filename} -> shapes/{table_name} "
                f"({inserted_shape_ids} shape IDs, {len(point_rows_to_insert)} rows)"
            )
            return inserted_shape_ids + len(point_rows_to_insert)

    type_map = COLUMN_TYPES.get(table_name, {})

    with open(file_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            print(f"  [skip] {filename} is empty.")
            return 0

        header = [h.strip() for h in header]
        placeholders = ", ".join("?" for _ in header)
        columns_sql = ", ".join(f'"{h}"' for h in header)
        insert_sql = f'INSERT INTO {table_name} ({columns_sql}) VALUES ({placeholders})'

        rows_to_insert = []
        for row in reader:
            if not row or all(cell.strip() == "" for cell in row):
                continue
            
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            elif len(row) > len(header):
                row = row[: len(header)]

            coerced = [
                coerce_value(value, type_map.get(col))
                for col, value in zip(header, row)
            ]
            rows_to_insert.append(coerced)

        if rows_to_insert:
            conn.executemany(insert_sql, rows_to_insert)

        print(f"  [ok]   {filename} -> {table_name} ({len(rows_to_insert)} rows)")
        return len(rows_to_insert)


def build_database(input_path, output_path, schema_path=DEFAULT_SCHEMA_PATH, overwrite=True):
    if os.path.exists(output_path):
        if not overwrite:
            raise FileExistsError(
                f"Output file already exists: {output_path} (use --overwrite or remove --no-overwrite)"
            )
        os.remove(output_path)

    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    schema_sql = load_schema_sql(schema_path)

    conn = sqlite3.connect(output_path)
    try:
        conn.executescript(schema_sql)

        with resolve_input_path(input_path) as resolved_input_path:
            with resolve_gtfs_dir(resolved_input_path) as gtfs_dir:
                print(f"Loading GTFS files from: {gtfs_dir}")
                total_rows = 0
                for filename, table_name in FILE_TO_TABLE:
                    total_rows += load_file_into_table(conn, gtfs_dir, filename, table_name)

        conn.commit()

        # FK enforcement is intentionally left off for this database.

        print(f"\nDone. Total rows inserted: {total_rows}")
        print(f"Database written to: {os.path.abspath(output_path)}")
    finally:
        conn.close()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert a GTFS Schedule feed (zip or unzipped directory) into a SQLite database."
    )
    parser.add_argument(
        "--input-url", "-i", required=True,
        help="Path/URL to the GTFS zip file, or a directory containing unzipped GTFS .txt files.",
    )
    parser.add_argument(
        "--output", "-o", default=os.path.join(os.getcwd(), DEFAULT_OUTPUT_FILENAME),
        help=(
            "Path to the SQLite database file to create. "
            f"Defaults to {DEFAULT_OUTPUT_FILENAME} in the current working directory."
        ),
    )
    parser.add_argument(
        "--no-overwrite", action="store_true",
        help="Abort if the output database file already exists, instead of overwriting it.",
    )
    parser.add_argument(
        "--schema", "-s", default=DEFAULT_SCHEMA_PATH,
        help=f"Path to the schema.sql file defining the database structure. "
             f"Defaults to schema.sql next to this script ({DEFAULT_SCHEMA_PATH}).",
    )
    return parser.parse_args()

def trim_database(output_path):
    """remove all database records for routes that do not pass through the selected stops"""
    
    conn = sqlite3.connect(output_path)

    try:
        pass

    finally:
        conn.close()
def main():
    args = parse_args()

    if not is_http_url(args.input_url) and not os.path.exists(args.input_url):
        print(f"Error: input path does not exist: {args.input_url}", file=sys.stderr)
        sys.exit(1)

    try:
        build_database(args.input_url, args.output, schema_path=args.schema, overwrite=not args.no_overwrite)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()