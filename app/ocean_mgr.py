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
        self._edges: list = []
        self._is_hemisphere = self._detect_hemisphere()
        if not self._is_hemisphere:
            self._preprocess()

    def _detect_hemisphere(self):
        if len(self.vertices) != 2:
            return False
        (lat0, lon0), (lat1, lon1) = self.vertices
        # K28: 半球判定经度差用 ±360 等价距离(179.9 与 -179.9 是同一子午线)
        d = abs(lon0 - lon1) % 360.0
        if min(d, 360.0 - d) > 0.001:
            return False
        near_equator_a = abs(lat0) <= 0.11
        near_equator_b = abs(lat1) <= 0.11
        # 北/南半球覆盖区(北→南两极或南→北两极各两分支,合并判断)
        if (near_equator_a and lat1 >= 89.9) or (near_equator_b and lat0 >= 89.9):
            self._hemisphere_north = True
            return True
        if (near_equator_a and lat1 <= -89.9) or (near_equator_b and lat0 <= -89.9):
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
        verts = self._proc_vertices
        self._edges = [(xi, yi, xj - xi, yj - yi)
                       for (yi, xi), (yj, xj) in zip(verts, verts[1:] + verts[:1])]
        lats = [v[0] for v in verts]
        self._bbox = (min(lats), max(lats), min(lons2), max(lons2))

    def contains(self, lat, lon):
        if self._is_hemisphere:
            return lat >= 0 if self._hemisphere_north else lat < 0
        if len(self._proc_vertices) < 3:
            return False
        # bbox 预检：绝大多数查询在盒外，免全边扫描(法13)
        bmin_lat, bmax_lat, bmin_lon, bmax_lon = self._bbox
        if lat < bmin_lat or lat > bmax_lat:
            return False
        test_lon = lon
        while test_lon - self._proc_lon_center > 180: test_lon -= 360
        while test_lon - self._proc_lon_center < -180: test_lon += 360
        if test_lon < bmin_lon or test_lon > bmax_lon:
            return False
        inside = False
        for xi, yi, dx, dy in self._edges:
            if (yi > lat) != (yi + dy > lat):
                if test_lon < dx * (lat - yi) / dy + xi:
                    inside = not inside
        return inside


class OceanSubArea:
    """子洋区: 主洋区的矩形子集(显示用,不出现在 ACE 限制/自动检测中)。

    判定 = 定义矩形 ∩ 主洋区多边形(两者同时满足才属于子洋区),
    定义超出主洋区的部分自动裁掉。经度支持跨 180 子午线(lon_min > lon_max)。"""

    def __init__(self, code, parent_code, name_cn, name_full,
                 lon_min, lon_max, lat_min, lat_max, parent=None):
        self.code = code
        self.parent_code = parent_code
        self.name_cn = name_cn
        self.name_full = name_full
        self.lon_min = float(lon_min)
        self.lon_max = float(lon_max)
        self.lat_min = float(lat_min)
        self.lat_max = float(lat_max)
        self.parent = parent  # Optional[OceanArea],加载时挂接

    def _lon_inside(self, lon: float) -> bool:
        if self.lon_min <= self.lon_max:
            return self.lon_min <= lon <= self.lon_max
        # 跨 180 子午线(如 170E 以东 → 170..360)
        return lon >= self.lon_min or lon <= self.lon_max

    def contains(self, lat: float, lon: float) -> bool:
        if not (self.lat_min <= lat <= self.lat_max):
            return False
        if not self._lon_inside(lon):
            return False
        # 与主洋区求交集: 定义超出主洋区的部分不属于子洋区
        if self.parent is not None and not self.parent.contains(lat, lon):
            return False
        return True


# ── 子洋区定义(仅统计面板显示用,不在 ACE 限制/自动检测中出现) ──
# parent_code 为紧凑洋区文件中的主洋区二字码(如 西太 = WP)
_SUB_AREA_DEFS = [
    # LWPAC: 西太远洋低纬 = 145E 以东、20N 以南的 WP
    dict(code='LWPAC', parent_code='WP', name_cn='远洋低纬',
         name_full='西太远洋低纬',
         lon_min=145.0, lon_max=180.0, lat_min=-90.0, lat_max=20.0),
    # NWPAC: 西太高纬 = 25N 以北的西太
    dict(code='NWPAC', parent_code='WP', name_cn='高纬',
         name_full='西太高纬',
         lon_min=-180.0, lon_max=180.0, lat_min=25.0, lat_max=90.0),
]


class OceanAreaManager:
    def __init__(self):
        self.areas: List[OceanArea] = []
        self._by_code: dict = {}
        self.sub_areas: List[OceanSubArea] = []
        self._sub_by_code: dict = {}
        self._load()
        self._rebuild_by_code()
        self._load_sub_areas()

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
                    # N24: 坏行跳过,不中断启动
                    try:
                        self.areas.append(self._parse(line))
                    except (ValueError, IndexError) as e:
                        logger.warning(f"洋区行解析失败跳过: {e}")

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

    def _rebuild_by_code(self):
        self._by_code = {a.code: a for a in self.areas}

    def get_by_code(self, code):
        """O(1) 字典查(由 _rebuild_by_code 在加载时构建,无需运行时回退)。
        主洋区优先;子洋区作为显示用统计区域可透传(ACE 限制/自动检测不使用)。"""
        return self._by_code.get(code) or self._sub_by_code.get(code)

    # ── 子洋区 ──

    def _load_sub_areas(self):
        for d in _SUB_AREA_DEFS:
            try:
                parent = self._by_code.get(d['parent_code'])
                sub = OceanSubArea(
                    code=d['code'], parent_code=d['parent_code'],
                    name_cn=d['name_cn'], name_full=d['name_full'],
                    lon_min=d['lon_min'], lon_max=d['lon_max'],
                    lat_min=d['lat_min'], lat_max=d['lat_max'],
                    parent=parent)
                self.sub_areas.append(sub)
                self._sub_by_code[sub.code] = sub
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"子洋区定义跳过 {d.get('code', '?')}: {e}")

    def get_sub_by_code(self, code):
        return self._sub_by_code.get(code)

    def get_sub_areas(self, parent_code=None) -> List[OceanSubArea]:
        """返回子洋区列表; parent_code 指定时仅返回该主洋区的子洋区。"""
        if parent_code is None:
            return list(self.sub_areas)
        return [s for s in self.sub_areas if s.parent_code == parent_code]

    def find_sub_area(self, lat, lon):
        """返回包含该点的第一个子洋区(显示用),无则 None。"""
        for sub in self.sub_areas:
            if sub.contains(lat, lon):
                return sub
        return None
