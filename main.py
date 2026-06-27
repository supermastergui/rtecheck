"""
Route processing: FLIGHT_AIRLINE.csv + FLIGHT_AIRLINE_POINT.csv -> out/RC.csv

Logic:
1. Read FLIGHT_AIRLINE.csv, FLIGHT_AIRLINE_POINT.csv, ISEC.txt, ICAO_Airports.txt,
   ROUTE_RESTRICT.csv, ROUTE_RESTRICT_RTE.csv.
2. Build routes from points, with VOR/NDB->Identifier, airway compression.
3. EvenOdd: compute via ISEC coordinates (west=SE, east=SO).
4. AltList: from TRANS_ALT, formatted as "S84" / "S84 / S78 / S72".
5. NAIP detection: H/Z/J/X/V airways, FANS -> flag and output modified row.
6. ROUTE_RESTRICT remarks merged via AIRWAY_POINT_UUID matching.
"""

import csv
import os
import re
from collections import defaultdict

INPUT_DIR = os.path.join(os.path.dirname(__file__), "res")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "out")
ENCODING = "gb18030"

# ---------------------------------------------------------------------------
# ISEC coordinate cache
# ---------------------------------------------------------------------------
def load_isec_coords() -> dict:
    """Load ISEC.txt: name -> (lat, lon)."""
    coords = {}
    path = os.path.join(INPUT_DIR, "ISEC.txt")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name = parts[0].strip()
            try:
                lat = float(parts[1].strip())
                lon = float(parts[2].strip())
                coords[name] = (lat, lon)
            except (ValueError, IndexError):
                pass
    return coords


# ---------------------------------------------------------------------------
# Name resolution (VOR/NDB -> Identifier)
# ---------------------------------------------------------------------------
def resolve_name(name: str, identifier: str) -> str:
    """If name contains 'VOR' or 'NDB' and identifier exists, return identifier."""
    if ("VOR" in name or "NDB" in name) and identifier:
        return identifier
    return name


# ---------------------------------------------------------------------------
# AltList formatting
# ---------------------------------------------------------------------------
def format_altlist(trans_alt: str) -> str:
    """Convert TRANS_ALT: '84'->'S84', '84/78'->'S84 / S78', complex->as-is."""
    trans_alt = trans_alt.strip()
    if not trans_alt:
        return ""
    if re.fullmatch(r"[\d/]+", trans_alt):
        parts = trans_alt.split("/")
        return "/".join(f"S{p}" for p in parts)
    return trans_alt


# ---------------------------------------------------------------------------
# Route compression
# ---------------------------------------------------------------------------
def compress_route(parts: list) -> list:
    """Collapse consecutive identical airways."""
    if len(parts) < 3:
        return parts[:]
    result = [parts[0]]
    i = 1
    while i < len(parts) - 1:
        airway = parts[i]
        j = i + 2
        while j < len(parts) and parts[j] == airway:
            j += 2
        result.append(airway)
        last_idx = min(j - 1, len(parts) - 1)
        if last_idx >= 0:
            result.append(parts[last_idx])
        i = j
    return result


# ---------------------------------------------------------------------------
# EvenOdd: direction via ISEC coordinates
# ---------------------------------------------------------------------------
def compute_evenodd(pts_sorted: list, isec: dict) -> str:
    """
    Determine direction from first/last point coordinates in ISEC.
    West (end_lon < start_lon) -> 'SE', East -> 'SO'.
    Falls back to 'SE' if coordinates unavailable.
    """
    if not pts_sorted:
        return "SE"

    first_name = pts_sorted[0]["start"]
    last_name = pts_sorted[-1]["end"]

    i = 0
    while first_name not in isec and i < len(pts_sorted):
        p = pts_sorted[i]
        first_name = p["start"] if p["start"] in isec else p["end"]
        if first_name in isec:
            break
        i += 1

    j = len(pts_sorted) - 1
    while last_name not in isec and j >= 0:
        p = pts_sorted[j]
        last_name = p["end"] if p["end"] in isec else p["start"]
        if last_name in isec:
            break
        j -= 1

    if first_name in isec and last_name in isec:
        _, lon1 = isec[first_name]
        _, lon2 = isec[last_name]
        return "SE" if lon2 < lon1 else "SO"

    return "SE"


# ---------------------------------------------------------------------------
# NAIP route detection and cleaning
# ---------------------------------------------------------------------------
# NAIP airway pattern: single letter H/Z/J/X/V followed by digits, or FANS
NAIP_AIRWAY_RE = re.compile(r'^[HZJXV]\d+$')
RE_P_POINT = re.compile(r"^P\d+$")
FANS_KEYWORD = "FANS"


def has_naip_elements(parts: list) -> bool:
    """Check if compressed parts list contains any NAIP element."""
    for idx, elem in enumerate(parts):
        if idx % 2 == 0:
            if RE_P_POINT.match(elem):
                return True
        else:
            if elem == FANS_KEYWORD or NAIP_AIRWAY_RE.match(elem):
                return True
    return False


def _find_raw_intermediates(raw_parts: list, start_pt: str, end_pt: str) -> list:
    """Extract waypoints (even-index elements) between start_pt and end_pt in raw parts."""
    in_segment = False
    intermediates = []
    for i in range(0, len(raw_parts), 2):
        pt = raw_parts[i]
        if pt == start_pt:
            in_segment = True
            continue
        if pt == end_pt:
            break
        if in_segment:
            intermediates.append(pt)
    return intermediates


def build_modified_route(compressed_parts: list, raw_parts: list) -> str:
    """
    Build modified route. For NAIP airway segments, expand with non-NAIP
    intermediate waypoints from raw route data (direct connections).
    Skip P-points everywhere.
    """
    result = []
    for idx, elem in enumerate(compressed_parts):
        if idx % 2 == 1:  # airway position
            if NAIP_AIRWAY_RE.match(elem) or elem == FANS_KEYWORD:
                # NAIP airway: get non-P intermediate points from raw data
                start_pt = compressed_parts[idx - 1]
                end_pt = compressed_parts[idx + 1]
                intermediates = _find_raw_intermediates(raw_parts, start_pt, end_pt)
                # Keep only non-P-points as direct connection points
                filtered = [p for p in intermediates if not RE_P_POINT.match(p)]
                result.extend(filtered)
                continue
        else:  # point position
            if RE_P_POINT.match(elem):
                continue  # skip P-points
        result.append(elem)
    return " ".join(result)


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------
def read_airlines(path: str) -> dict:
    """Read FLIGHT_AIRLINE.csv, return dict keyed by FLIGHT_AIRLINE_ID."""
    airlines = {}
    with open(path, "r", encoding=ENCODING) as f:
        reader = csv.DictReader(f)
        for row in reader:
            aid = row["FLIGHT_AIRLINE_ID"]
            airlines[aid] = row
    return airlines


def read_points(path: str, airline_ids: set) -> dict:
    """Read FLIGHT_AIRLINE_POINT.csv, group by FLIGHT_AIRLINE_ID."""
    points = defaultdict(list)
    with open(path, "r", encoding=ENCODING) as f:
        reader = csv.DictReader(f)
        for row in reader:
            aid = row["FLIGHT_AIRLINE_ID"]
            if aid in airline_ids:
                raw_start = row["StartPointName"].strip()
                raw_end = row["EndPointName"].strip()
                start_idt = row["StartPointIdentifier"].strip()
                end_idt = row["EndPointIdentifier"].strip()
                points[aid].append({
                    "seq": int(row["Sequnce"]),
                    "start": resolve_name(raw_start, start_idt),
                    "end": resolve_name(raw_end, end_idt),
                    "airway": row["AirwayName"].strip(),
                })
    return points


def read_restrictions() -> dict:
    """Build FLIGHT_AIRLINE_ID -> SPECIAL_REMARK via AIRWAY_POINT_UUID."""
    restrict_path = os.path.join(INPUT_DIR, "ROUTE_RESTRICT.csv")
    restrict_rte_path = os.path.join(INPUT_DIR, "ROUTE_RESTRICT_RTE.csv")
    point_path = os.path.join(INPUT_DIR, "FLIGHT_AIRLINE_POINT.csv")

    restrict_map = {}
    with open(restrict_path, "r", encoding=ENCODING) as f:
        for row in csv.DictReader(f):
            rid = row["ROUTE_RESTRICT_ID"].strip()
            remark = row["SPECIAL_REMARK"].strip()
            if rid and remark:
                restrict_map[rid] = remark

    airway_to_restrict = {}
    with open(restrict_rte_path, "r", encoding=ENCODING) as f:
        for row in csv.DictReader(f):
            au = row["AIRWAY_POINT_UUID"].strip()
            rid = row["ROUTE_RESTRICT_ID"].strip()
            if au and rid:
                airway_to_restrict[au] = rid

    if not airway_to_restrict:
        return {}

    airline_remarks = defaultdict(set)
    with open(point_path, "r", encoding=ENCODING) as f:
        for row in csv.DictReader(f):
            sid = row["StartPointID"].strip()
            eid = row["EndPointID"].strip()
            matched_rid = None
            if sid and sid in airway_to_restrict:
                matched_rid = airway_to_restrict[sid]
            elif eid and eid in airway_to_restrict:
                matched_rid = airway_to_restrict[eid]
            if matched_rid and matched_rid in restrict_map:
                aid = row["FLIGHT_AIRLINE_ID"].strip()
                airline_remarks[aid].add(restrict_map[matched_rid])

    return {k: " | ".join(sorted(v)) for k, v in airline_remarks.items()}


# ---------------------------------------------------------------------------
# Route builder
# ---------------------------------------------------------------------------
def build_route_parts(points: list) -> list:
    """Build route parts list from sorted segments, then compress."""
    pts = sorted(points, key=lambda x: x["seq"])
    parts = []
    prev_end = ""
    for p in pts:
        if not prev_end:
            if p["start"]:
                parts.append(p["start"])
            if p["airway"]:
                parts.append(p["airway"])
            if p["end"]:
                parts.append(p["end"])
        else:
            if p["start"] == prev_end:
                if p["airway"]:
                    parts.append(p["airway"])
                if p["end"]:
                    parts.append(p["end"])
            else:
                if p["start"]:
                    parts.append(p["start"])
                if p["airway"]:
                    parts.append(p["airway"])
                if p["end"]:
                    parts.append(p["end"])
        prev_end = p["end"]

    parts = compress_route(parts)
    return parts


def build_route_parts_raw(points: list) -> list:
    """Build route parts list from sorted segments, WITHOUT airway compression."""
    pts = sorted(points, key=lambda x: x["seq"])
    parts = []
    prev_end = ""
    for p in pts:
        if not prev_end:
            if p["start"]:
                parts.append(p["start"])
            if p["airway"]:
                parts.append(p["airway"])
            if p["end"]:
                parts.append(p["end"])
        else:
            if p["start"] == prev_end:
                if p["airway"]:
                    parts.append(p["airway"])
                if p["end"]:
                    parts.append(p["end"])
            else:
                if p["start"]:
                    parts.append(p["start"])
                if p["airway"]:
                    parts.append(p["airway"])
                if p["end"]:
                    parts.append(p["end"])
        prev_end = p["end"]
    return parts


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    airline_path = os.path.join(INPUT_DIR, "FLIGHT_AIRLINE.csv")
    point_path = os.path.join(INPUT_DIR, "FLIGHT_AIRLINE_POINT.csv")
    output_path = os.path.join(OUTPUT_DIR, "RC.csv")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Loading ISEC coordinates ...")
    isec = load_isec_coords()
    print(f"  {len(isec)} waypoints loaded.")

    print("Reading FLIGHT_AIRLINE.csv ...")
    airlines = read_airlines(airline_path)
    print(f"  {len(airlines)} airlines loaded.")

    print("Reading FLIGHT_AIRLINE_POINT.csv ...")
    points_by_airline = read_points(point_path, set(airlines.keys()))
    total_points = sum(len(v) for v in points_by_airline.values())
    print(f"  {total_points} points loaded into {len(points_by_airline)} groups.")

    print("Reading ROUTE_RESTRICT data ...")
    remarks_map = read_restrictions()
    print(f"  {len(remarks_map)} airlines with restriction remarks.")

    print("Building routes and writing out/RC.csv ...")
    count = 0
    naip_count = 0
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Dep", "Arr", "Name", "EvenOdd", "AltList", "MinAlt", "Route", "Remarks"])

        sorted_airlines = sorted(airlines.items(), key=lambda x: x[1].get("name", ""))

        for aid, airline in sorted_airlines:
            pts = points_by_airline.get(aid, [])
            if not pts:
                continue

            pts_sorted = sorted(pts, key=lambda x: x["seq"])
            evenodd = compute_evenodd(pts_sorted, isec)
            parts = build_route_parts(pts)
            altlist = format_altlist(airline.get("TRANS_ALT", ""))
            base_remarks = remarks_map.get(aid, "")
            name = airline["name"]
            route_full = " ".join(parts)

            # Detect NAIP
            is_naip = has_naip_elements(parts)

            # --- Row 1: Original route (full, no truncation) ---
            row1_remarks = base_remarks
            if is_naip:
                row1_remarks = (row1_remarks + " | " if row1_remarks else "") + "!!!NAIP Route!!!"
                naip_count += 1

            writer.writerow([
                airline["StartAirportID"],
                airline["EndAirportID"],
                name,
                evenodd,
                altlist,
                airline["MinSafeAltitude"],
                route_full,
                row1_remarks,
            ])
            count += 1

            # --- Row 2 (NAIP only): Modified route, Name + "-Modified" ---
            if is_naip:
                raw_parts = build_route_parts_raw(pts)
                modified_route = build_modified_route(parts, raw_parts)
                row2_remarks = (base_remarks + " | " if base_remarks else "") + "!!!NAIP Route!!! Modified Route!!!"
                writer.writerow([
                    airline["StartAirportID"],
                    airline["EndAirportID"],
                    name + "-Modified",
                    evenodd,
                    altlist,
                    airline["MinSafeAltitude"],
                    modified_route,
                    row2_remarks,
                ])
                count += 1

    print(f"  Done. {count} rows written, {naip_count} NAIP flagged.")

    # -----------------------------------------------------------------------
    # FULL.csv: all airlines, route = points only (direct flights, no airways)
    # -----------------------------------------------------------------------
    full_path = os.path.join(OUTPUT_DIR, "FULL.csv")
    print("Writing out/FULL.csv (points only, direct flights) ...")
    full_count = 0
    with open(full_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Dep", "Arr", "Name", "EvenOdd", "AltList", "MinAlt", "Route", "Remarks"])

        for aid, airline in sorted_airlines:
            pts = points_by_airline.get(aid, [])
            if not pts:
                continue

            pts_sorted = sorted(pts, key=lambda x: x["seq"])
            evenodd = compute_evenodd(pts_sorted, isec)
            parts = build_route_parts_raw(pts)
            altlist = format_altlist(airline.get("TRANS_ALT", ""))
            base_remarks = remarks_map.get(aid, "")
            name = airline["name"]

            # Extract only waypoints (even indices) -> direct flight route
            direct_parts = [elem for idx, elem in enumerate(parts) if idx % 2 == 0]
            route_direct = " ".join(direct_parts)

            writer.writerow([
                airline["StartAirportID"],
                airline["EndAirportID"],
                name,
                evenodd,
                altlist,
                airline["MinSafeAltitude"],
                route_direct,
                base_remarks,
            ])
            full_count += 1

    print(f"  Done. {full_count} rows written.")


if __name__ == "__main__":
    main()
