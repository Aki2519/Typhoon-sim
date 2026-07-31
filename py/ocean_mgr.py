from __future__ import annotations

import os
import json
import logging
from typing import List, Tuple

from .constants import (
    SUCAI_DIR, AREA_OCEAN_FILE,
    find_insensitive_path as fip
)

logger = logging.getLogger(__name__)


class OceanArea:
    def __init__(self, code, name_cn, name_full, hemisphere, avg_ace, vertices, is_merged=False):
        self.code = code
        self.name_cn = name_cn
        self.name_full = name_full
        self.hemisphere = hemisphere
        self.avg_ace = avg_ace
        self.vertices = vertices
        self.is_merged = is_merged
        self._proc_vertices: List[Tuple[float, float]] = []
        self._proc_lon_center: float = 0.0
        self._is_hemisphere = self._detect_hemisphere()
        if not self._is_hemisphere:
            self._preprocess()

    def _detect_hemisphere(self):
        if len(self.vertices) != 2:
            return False
        (lat0, lon0), (lat1, lon1) = self.vertices
        if abs(lon0 - lon1) > 0.001:
            return False
        if abs(lat0) <= 0.11 and lat1 >= 89.9:
            self._hemisphere_north = True
            return True
        if abs(lat1) <= 0.11 and lat0 >= 89.9:
            self._hemisphere_north = True
            return True
        if abs(lat0) <= 0.11 and lat1 <= -89.9:
            self._hemisphere_north = False
            return True
        if abs(lat1) <= 0.11 and lat0 <= -89.9:
            self._hemisphere_north = False
            return True
        return False

    def _preprocess(self):
        count = len(self.vertices)
        if count < 3:
            self._proc_vertices = list(self.vertices)
            return
        unwrapped = [[self.vertices[0][0], self.vertices[0][1]]]
        for i in range(1, count):
            prev_lon = unwrapped[-1][1]
            cur_lat, cur_lon = self.vertices[i]
            while cur_lon - prev_lon > 180: cur_lon -= 360
            while cur_lon - prev_lon < -180: cur_lon += 360
            unwrapped.append([cur_lat, cur_lon])
        lons = [v[1] for v in unwrapped]
        min_lon, max_lon = min(lons), max(lons)
        span = max_lon - min_lon
        if span > 180:
            center = (min_lon + max_lon) / 2.0
            shifted = []
            for lat, lon in unwrapped:
                while lon - center > 180: lon -= 360
                while lon - center < -180: lon += 360
                shifted.append((lat, lon))
            self._proc_vertices = shifted
        else:
            self._proc_vertices = [(v[0], v[1]) for v in unwrapped]
        lons2 = [v[1] for v in self._proc_vertices]
        self._proc_lon_center = (min(lons2) + max(lons2)) / 2.0

    def contains(self, lat, lon):
        if self._is_hemisphere:
            return lat >= 0 if self._hemisphere_north else lat < 0
        verts = self._proc_vertices
        if len(verts) < 3:
            return False
        test_lon = lon
        while test_lon - self._proc_lon_center > 180: test_lon -= 360
        while test_lon - self._proc_lon_center < -180: test_lon += 360
        inside = False
        j = len(verts) - 1
        for i in range(len(verts)):
            yi, xi = verts[i]
            yj, xj = verts[j]
            if (yi > lat) != (yj > lat):
                if test_lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                    inside = not inside
            j = i
        return inside


class OceanAreaManager:
    def __init__(self):
        self.areas: List[OceanArea] = []
        self._load()

    @property
    def total_avg_ace(self):
        return sum(a.avg_ace for a in self.areas if not a.is_merged)

    def _load(self):
        geojson_path = os.path.join(SUCAI_DIR, "Area_ocean.geojson")
        compact_path = os.path.join(SUCAI_DIR, "Area_ocean.json")
        if os.path.exists(compact_path):
            self._load_compact(compact_path)
            if self.areas:
                return
        if os.path.exists(geojson_path):
            self._load_geojson(geojson_path)
            if self.areas:
                return
        path = fip(AREA_OCEAN_FILE)
        if not path:
            logger.warning(f"洋区文件不存在: {AREA_OCEAN_FILE}")
            return
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    self.areas.append(self._parse(line))

    def _load_compact(self, path: str):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"紧凑 JSON 加载失败: {e}")
            return
        if not isinstance(data, list):
            logger.warning(f"紧凑 JSON 顶层不是列表: {path}")
            return
        for obj in data:
            try:
                if not isinstance(obj, dict) or not isinstance(obj.get('c'), list):
                    raise ValueError("缺少坐标列表")
                verts = [(float(lat), float(lon)) for lon, lat in obj['c']]
                try:
                    avg_ace = float(obj.get('ace', 0))
                except (TypeError, ValueError):
                    avg_ace = 0.0
                area = OceanArea(
                    code=obj.get('code', ''),
                    name_cn=obj.get('name_cn', ''),
                    name_full=obj.get('name_full', ''),
                    hemisphere=obj.get('h', 'N'),
                    avg_ace=avg_ace,
                    vertices=verts,
                    is_merged=bool(obj.get('merged', False)),
                )
                self.areas.append(area)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"紧凑 JSON 条目跳过: {e}")

    def _load_geojson(self, path: str):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"GeoJSON 加载失败: {e}")
            return
        features = data.get("features", []) if isinstance(data, dict) else []
        for feat in features:
            try:
                props = feat.get("properties", {})
                geom = feat.get("geometry", {})
                if geom.get("type") != "Polygon":
                    continue
                coords = geom.get("coordinates", [[]])[0]
                verts = [(float(lat), float(lon)) for lon, lat in coords]
                try:
                    avg_ace = float(props.get("avg_ace", 0))
                except (TypeError, ValueError):
                    avg_ace = 0.0
                area = OceanArea(
                    code=props.get("code", ""),
                    name_cn=props.get("name_cn", ""),
                    name_full=props.get("name_full", ""),
                    hemisphere=props.get("hemisphere", "N"),
                    avg_ace=avg_ace,
                    vertices=verts,
                    is_merged=bool(props.get("is_merged", False)),
                )
                self.areas.append(area)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"GeoJSON 条目跳过: {e}")

    def _parse(self, line):
        is_merged = line.startswith('(') and line.endswith(')')
        if is_merged:
            line = line[1:-1]
        parts = line.rsplit('/', 4)
        header, name_cn = parts[0], parts[1] if len(parts) > 1 else ""
        name_full = parts[2] if len(parts) > 2 else name_cn
        hemi = parts[3].strip().upper() if len(parts) > 3 else "N"
        avg_ace_raw = parts[4].strip().rstrip(')') if len(parts) > 4 else "0"
        avg_ace = float(avg_ace_raw) if avg_ace_raw else 0.0
        code = header.split(';')[0].strip()
        verts = []
        for s in header.split(';')[1:]:
            toks = s.strip().split()
            if len(toks) >= 2:
                verts.append((self._plat(toks[0]), self._plon(toks[1])))
        return OceanArea(code, name_cn.strip(), name_full.strip(), hemi, avg_ace, verts, is_merged)

    @staticmethod
    def _plat(s):
        s = s.strip().upper()
        if s.endswith('S'):  return -float(s[:-1])
        if s.endswith('N'):  return float(s[:-1])
        return float(s)

    @staticmethod
    def _plon(s):
        s = s.strip().upper()
        v = float(s[:-1]) if s.endswith(('E', 'W')) else float(s)
        if s.endswith('W') and abs(v - 180) > 0.001 and abs(v) > 0.001:
            return 360.0 - v
        return v

    def find_area(self, lat, lon):
        for area in self.areas:
            if area.contains(lat, lon):
                return area
        return None

    def get_by_code(self, code):
        for a in self.areas:
            if a.code == code:
                return a
        return None
