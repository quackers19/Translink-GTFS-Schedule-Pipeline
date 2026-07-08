PRAGMA foreign_keys = ON;


-- ============================================================
-- agency.txt
-- ============================================================

CREATE TABLE agency (
    agency_id TEXT PRIMARY KEY,
    agency_name TEXT NOT NULL,
    agency_url TEXT NOT NULL,
    agency_timezone TEXT NOT NULL,
    agency_lang TEXT,
    agency_phone TEXT,
    agency_fare_url TEXT,
    agency_email TEXT
);


-- ============================================================
-- feed_info.txt
-- ============================================================

CREATE TABLE feed_info (
    feed_publisher_name TEXT NOT NULL,
    feed_publisher_url TEXT NOT NULL,
    feed_lang TEXT NOT NULL,
    default_lang TEXT,
    feed_start_date TEXT,
    feed_end_date TEXT,
    feed_version TEXT,
    feed_contact_email TEXT,
    feed_contact_url TEXT
);


-- ============================================================
-- stops.txt
-- ============================================================

CREATE TABLE stops (
    stop_id TEXT PRIMARY KEY,
    stop_code TEXT,
    stop_name TEXT NOT NULL,
    tts_stop_name TEXT,
    stop_desc TEXT,
    stop_lat REAL NOT NULL,
    stop_lon REAL NOT NULL,
    zone_id TEXT,
    stop_url TEXT,
    location_type INTEGER,
    parent_station TEXT,
    stop_timezone TEXT,
    wheelchair_boarding INTEGER,
    level_id TEXT,
    platform_code TEXT
);


-- ============================================================
-- routes.txt
-- ============================================================

CREATE TABLE routes (
    route_id TEXT PRIMARY KEY,
    agency_id TEXT,
    route_short_name TEXT,
    route_long_name TEXT,
    route_desc TEXT,
    route_type INTEGER NOT NULL,
    route_url TEXT,
    route_color TEXT,
    route_text_color TEXT,
    route_sort_order INTEGER,
    continuous_pickup INTEGER,
    continuous_drop_off INTEGER,

    FOREIGN KEY (agency_id)
        REFERENCES agency(agency_id)
);


-- ============================================================
-- calendar.txt
-- ============================================================

CREATE TABLE calendar (
    service_id TEXT PRIMARY KEY,
    monday INTEGER NOT NULL,
    tuesday INTEGER NOT NULL,
    wednesday INTEGER NOT NULL,
    thursday INTEGER NOT NULL,
    friday INTEGER NOT NULL,
    saturday INTEGER NOT NULL,
    sunday INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL
);


-- ============================================================
-- calendar_dates.txt
-- ============================================================

CREATE TABLE calendar_dates (
    service_id TEXT NOT NULL,
    date TEXT NOT NULL,
    exception_type INTEGER NOT NULL,

    PRIMARY KEY (
        service_id,
        date
    ),

    FOREIGN KEY (service_id)
        REFERENCES calendar(service_id)
);


-- ============================================================
-- trips.txt
-- ============================================================

CREATE TABLE trips (
    route_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    trip_id TEXT PRIMARY KEY,
    trip_headsign TEXT,
    trip_short_name TEXT,
    direction_id INTEGER,
    block_id TEXT,
    shape_id TEXT,
    wheelchair_accessible INTEGER,
    bikes_allowed INTEGER,

    FOREIGN KEY (route_id)
        REFERENCES routes(route_id),

    FOREIGN KEY (service_id)
        REFERENCES calendar(service_id),

    FOREIGN KEY (shape_id)
        REFERENCES shapes(shape_id)
);


-- ============================================================
-- stop_times.txt
-- ============================================================

CREATE TABLE stop_times (
    trip_id TEXT NOT NULL,
    arrival_time TEXT NOT NULL,
    departure_time TEXT NOT NULL,
    stop_id TEXT NOT NULL,
    stop_sequence INTEGER NOT NULL,
    stop_headsign TEXT,
    pickup_type INTEGER,
    drop_off_type INTEGER,
    continuous_pickup INTEGER,
    continuous_drop_off INTEGER,
    shape_dist_traveled REAL,
    timepoint INTEGER,

    PRIMARY KEY (
        trip_id,
        stop_sequence
    ),

    FOREIGN KEY (trip_id)
        REFERENCES trips(trip_id),

    FOREIGN KEY (stop_id)
        REFERENCES stops(stop_id)
);


-- ============================================================
-- shapes.txt
-- ============================================================

CREATE TABLE shapes (
    shape_id TEXT PRIMARY KEY
);


-- ============================================================
-- shape_points.txt
-- ============================================================

CREATE TABLE shape_points (
    shape_id TEXT NOT NULL,
    shape_pt_lat REAL NOT NULL,
    shape_pt_lon REAL NOT NULL,
    shape_pt_sequence INTEGER NOT NULL,
    shape_dist_traveled REAL,

    PRIMARY KEY (
        shape_id,
        shape_pt_sequence
    ),

    FOREIGN KEY (shape_id)
        REFERENCES shapes(shape_id)
);


-- ============================================================
-- INDEXES
-- ============================================================

-- Find all trips using a route
CREATE INDEX idx_trips_route
ON trips(route_id);


-- Find trips by service day
CREATE INDEX idx_trips_service
ON trips(service_id);


-- Find departures from a stop
CREATE INDEX idx_stop_times_stop
ON stop_times(stop_id);


-- Find stop sequence for a trip
CREATE INDEX idx_stop_times_trip
ON stop_times(trip_id);


-- Shape lookup
CREATE INDEX idx_shapes_id
ON shapes(shape_id);


-- Shape point lookup
CREATE INDEX idx_shape_points_id
ON shape_points(shape_id);


-- Route lookup by agency
CREATE INDEX idx_routes_agency
ON routes(agency_id);


-- Calendar date lookup
CREATE INDEX idx_calendar_dates_service
ON calendar_dates(service_id);