import argparse
import csv
import os
import sqlite3
import sys
import re
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


def normalize_csv_row(row, header):
    if not row or all(cell.strip() == "" for cell in row):
        return None

    if len(row) < len(header):
        return row + [""] * (len(header) - len(row))
    if len(row) > len(header):
        return row[: len(header)]
    return row


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
                row = normalize_csv_row(row, header)
                if row is None:
                    continue

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
            row = normalize_csv_row(row, header)
            if row is None:
                continue

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
        "--url", "-i", required=True,
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
    parser.add_argument(
        "--stop-ids", default="",
        help="Optional comma-separated list of stop IDs to keep when trimming the database.",
    )
    return parser.parse_args()

def parse_stop_ids(stop_ids_value):
    if not stop_ids_value:
        return []

    stop_ids = []
    seen = set()
    for stop_id in re.split(r"[,\n]+", stop_ids_value):
        normalized_stop_id = stop_id.strip()
        if normalized_stop_id and normalized_stop_id not in seen:
            seen.add(normalized_stop_id)
            stop_ids.append(normalized_stop_id)
    return stop_ids


def trim_database(output_path, stop_ids):
    """Remove records that are not reachable from the selected stop IDs."""
    if not stop_ids:
        print("Skipping trim: no stop IDs were provided.")
        return

    def execute_delete_sql(conn, sql, params=()):
        deleted = conn.execute(sql, params).rowcount
        return deleted if deleted != -1 else 0

    def execute_delete(conn, table_name, column_name, keep_values):
        if not keep_values:
            return execute_delete_sql(conn, f"DELETE FROM {table_name}")

        placeholders = ", ".join("?" for _ in keep_values)
        sql = f"DELETE FROM {table_name} WHERE {column_name} NOT IN ({placeholders})"
        return execute_delete_sql(conn, sql, keep_values)

    conn = sqlite3.connect(output_path)

    try:
        conn.execute("PRAGMA foreign_keys = OFF")

        selected_stop_ids = list(stop_ids)
        stop_placeholders = ", ".join("?" for _ in selected_stop_ids)
        relevant_trip_ids = [
            row[0]
            for row in conn.execute(
                f"SELECT DISTINCT trip_id FROM stop_times WHERE stop_id IN ({stop_placeholders})",
                selected_stop_ids,
            )
        ]

        relevant_route_ids = set()
        relevant_service_ids = set()
        relevant_shape_ids = set()
        relevant_agency_ids = set()
        relevant_stop_ids = set()

        if relevant_trip_ids:
            trip_placeholders = ", ".join("?" for _ in relevant_trip_ids)
            for route_id, service_id, shape_id in conn.execute(
                f"SELECT route_id, service_id, shape_id FROM trips WHERE trip_id IN ({trip_placeholders})",
                relevant_trip_ids,
            ):
                if route_id:
                    relevant_route_ids.add(route_id)
                if service_id:
                    relevant_service_ids.add(service_id)
                if shape_id:
                    relevant_shape_ids.add(shape_id)

            for (stop_id,) in conn.execute(
                f"SELECT DISTINCT stop_id FROM stop_times WHERE trip_id IN ({trip_placeholders})",
                relevant_trip_ids,
            ):
                if stop_id:
                    relevant_stop_ids.add(stop_id)

        if relevant_route_ids:
            route_placeholders = ", ".join("?" for _ in relevant_route_ids)
            for (agency_id,) in conn.execute(
                f"SELECT DISTINCT agency_id FROM routes WHERE route_id IN ({route_placeholders})",
                list(relevant_route_ids),
            ):
                if agency_id:
                    relevant_agency_ids.add(agency_id)

        if relevant_trip_ids and relevant_stop_ids:
            trip_placeholders = ", ".join("?" for _ in relevant_trip_ids)
            stop_placeholders = ", ".join("?" for _ in relevant_stop_ids)
            deleted_stop_times = execute_delete_sql(
                conn,
                f"DELETE FROM stop_times WHERE trip_id NOT IN ({trip_placeholders}) OR stop_id NOT IN ({stop_placeholders})",
                relevant_trip_ids + list(relevant_stop_ids),
            )
        else:
            deleted_stop_times = execute_delete_sql(conn, "DELETE FROM stop_times")
        deleted_trips = execute_delete(conn, "trips", "trip_id", relevant_trip_ids)
        deleted_routes = execute_delete(conn, "routes", "route_id", list(relevant_route_ids))
        deleted_calendar_dates = execute_delete(conn, "calendar_dates", "service_id", list(relevant_service_ids))
        deleted_calendar = execute_delete(conn, "calendar", "service_id", list(relevant_service_ids))
        deleted_shape_points = execute_delete(conn, "shape_points", "shape_id", list(relevant_shape_ids))
        deleted_shapes = execute_delete(conn, "shapes", "shape_id", list(relevant_shape_ids))
        deleted_stops = execute_delete(conn, "stops", "stop_id", list(relevant_stop_ids))
        deleted_agency = execute_delete(conn, "agency", "agency_id", list(relevant_agency_ids))

        conn.commit()
        conn.execute("VACUUM")

        print("Trimmed database using stop IDs:", ", ".join(selected_stop_ids))
        print(
            "Removed rows -> "
            f"stop_times: {deleted_stop_times}, trips: {deleted_trips}, routes: {deleted_routes}, "
            f"calendar_dates: {deleted_calendar_dates}, calendar: {deleted_calendar}, "
            f"shape_points: {deleted_shape_points}, shapes: {deleted_shapes}, stops: {deleted_stops}, agency: {deleted_agency}"
        )
    finally:
        conn.close()
def main():
    args = parse_args()

    if not is_http_url(args.url) and not os.path.exists(args.url):
        print(f"Error: input path does not exist: {args.url}", file=sys.stderr)
        sys.exit(1)

    try:
        build_database(args.url, args.output, schema_path=args.schema, overwrite=not args.no_overwrite)
        stop_ids = parse_stop_ids(args.stop_ids)
        if stop_ids:
            trim_database(args.output, stop_ids)
    except (FileNotFoundError, ValueError, sqlite3.Error, OSError, zipfile.BadZipFile) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()