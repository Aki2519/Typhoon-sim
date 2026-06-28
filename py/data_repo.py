# py/data_repo.py
"""台风数据仓库：列表管理、文件加载、颜色/显示映射。"""
from __future__ import annotations

import os
import re
import glob
import csv
import logging
from typing import List, Optional, Tuple, TYPE_CHECKING

from .constants import (
    TYPHOON_DIR,
    FILE_FORMAT_SIMPLE_BDECK, FILE_FORMAT_JTWC,
)
from .typhoon import Typhoon, TrackPoint
from .utils import infer_strength_category, darken_color as _darken_color
from .constants import DB, EX, TD, TS, STS, C1, C2, C3, C4, C5_L, C5_D, MD_COLOR, C2_MINUS, C3_MINUS, C4_ST, WV

if TYPE_CHECKING:
    from .resource_manager import ResourceManager, OceanArea
    from .config import AppConfig
    from .ace_engine import ACEEngine
    from .ty_sim import TySim

logger = logging.getLogger(__name__)


class DataRepository:
    """台风数据仓库：持有台风列表并管理数据加载/解析/过滤。"""

    def __init__(self, cfg: AppConfig, res_mgr: ResourceManager) -> None:
        self.cfg = cfg
        self.res_mgr = res_mgr
        self.tys: List[Typhoon] = []
        self._all_tys_backup: List[Typhoon] = []
        self.cti: int = 0
        self.edit_typhoon: Optional[Typhoon] = None
        self._ace_engine: Optional[ACEEngine] = None
        self._sim: Optional[TySim] = None

    def bind(self, sim: TySim) -> None:
        self._sim = sim
        self._ace_engine = sim.ace_engine

    @property
    def ace_engine(self) -> Optional[ACEEngine]:
        return self._ace_engine

    @staticmethod
    def detect_format(filepath: str) -> str:
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    parts = line.split(',')
                    if len(parts) >= 11:
                        col5 = parts[4].strip().upper() if len(parts) > 4 else ''
                        if col5 in ('BEST', 'CARQ', 'OFCL', 'OFCI'):
                            return FILE_FORMAT_JTWC
                        return FILE_FORMAT_SIMPLE_BDECK
        except Exception:
            logger.debug(f"格式检测失败: {filepath}", exc_info=True)
        return FILE_FORMAT_SIMPLE_BDECK

    @staticmethod
    def convert_jtwc_to_simple_bdeck(input_path: str, output_path: str) -> None:
        with open(input_path, 'r', encoding='utf-8', errors='ignore') as infile, \
             open(output_path, 'w', newline='', encoding='utf-8') as outfile:
            reader = csv.reader(infile)
            writer = csv.writer(outfile)
            for row in reader:
                if len(row) < 12:
                    continue
                col12 = row[11].strip()
                if col12 in ('50', '64'):
                    continue
                new_row = row[:11] + ([row[27]] if len(row) > 27 else [])
                if len(new_row) > 4:
                    new_row[4] = 'chunshu'
                writer.writerow(new_row)

    def ensure_simple_bdeck_copy(self, ty: Typhoon) -> None:
        if ty.format_type == FILE_FORMAT_JTWC and ty.original_jtwc_source:
            original_path = ty.original_jtwc_source
            dir_name = os.path.dirname(original_path)
            base_name = os.path.splitext(os.path.basename(original_path))[0]
            new_path = os.path.join(dir_name, f"{base_name}_ty.txt")
            if not os.path.exists(new_path):
                self.convert_jtwc_to_simple_bdeck(original_path, new_path)
            ty.filepath = new_path
            ty.format_type = FILE_FORMAT_SIMPLE_BDECK
            ty.original_jtwc_source = None

    def load_typhoon_files(self) -> None:
        self.tys.clear()
        if not os.path.exists(TYPHOON_DIR):
            os.makedirs(TYPHOON_DIR)
            return
        for ext in ("*.txt", "*.dat"):
            for fp in glob.glob(os.path.join(TYPHOON_DIR, ext)):
                if os.path.isfile(fp):
                    self.parse_typhoon_file(fp)
        self._all_tys_backup = list(self.tys)
        if (getattr(self.cfg, 'basin_filter_enabled', True) and
                self.cfg.ace_limit_mode == "basin" and self.cfg.ace_limit_basin):
            area = self.res_mgr.ocean_areas.get_by_code(self.cfg.ace_limit_basin)
            if area is not None:
                self.tys = [ty for ty in self.tys if any(
                    area.contains(p['la'], p['lo']) for p in ty.pts)]
        self._sort_by_basin()
        self._refresh_ace()
        self._fill_point_categories()

    def parse_typhoon_file(self, fp: str, add_to_list: bool = True) -> Optional[Typhoon]:
        encodings = ['utf-8', 'gbk', 'latin-1', 'cp1252']
        lines = None
        for enc in encodings:
            try:
                with open(fp, 'r', encoding=enc) as f:
                    lines = f.readlines()
                break
            except UnicodeDecodeError:
                continue
        if lines is None:
            try:
                with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
            except (IOError, OSError) as e:
                logger.error(f"无法读取文件 {fp}: {e}")
                if self._sim:
                    self._sim.show_error(f"无法读取文件 {os.path.basename(fp)}")
                return None

        fmt = self.detect_format(fp)
        fn = os.path.basename(fp)
        m = re.search(r'(\d+)', fn)
        tn = m.group(1) if m else "01"
        ty = Typhoon("WP", tn)
        ty.sim = self._sim
        ty.filepath = fp
        ty.format_type = fmt
        if fmt == FILE_FORMAT_JTWC:
            ty.original_jtwc_source = fp

        last_basin = None
        for line in lines:
            if not line.strip() or line.startswith('#'):
                continue
            parts = line.split(',')
            if len(parts) >= 1:
                last_basin = parts[0].strip()
        if last_basin:
            ty.basin = last_basin

        for line in lines:
            if not line.strip() or line.startswith('#'):
                continue
            parts = line.split(',')
            if len(parts) < 11:
                continue
            try:
                ts = parts[2].strip()
                file_number = parts[1].strip()
                if file_number:
                    ty.n = file_number

                if fmt == FILE_FORMAT_JTWC:
                    if len(parts) > 5 and parts[5].strip() != '0':
                        continue
                    lat_str = parts[6].strip()
                    lat_val = float(lat_str[:-1]) / 10.0
                    if lat_str.endswith('S'):
                        lat_val = -lat_val
                    lon_str = parts[7].strip()
                    lon_val = float(lon_str[:-1]) / 10.0
                    if lon_str.endswith('W'):
                        lon_val = 360.0 - lon_val
                    w = int(parts[8].strip()) if parts[8].strip().isdigit() else 0
                    p = int(parts[9].strip()) if parts[9].strip().isdigit() else 0
                    st = parts[10].strip()
                    sn = parts[27].strip() if len(parts) > 27 else ""
                else:
                    lat_str = parts[6].strip()
                    lon_str = parts[7].strip()
                    lat_val = float(lat_str[:-1]) / 10.0
                    if lat_str.endswith('S'):
                        lat_val = -lat_val
                    lon_val = float(lon_str[:-1]) / 10.0
                    if lon_str.endswith('W'):
                        lon_val = 360.0 - lon_val
                    w = int(parts[8].strip()) if parts[8].strip().isdigit() else 0
                    p = int(parts[9].strip()) if parts[9].strip().isdigit() else 0
                    st = parts[10].strip()
                    sn = parts[11].strip() if len(parts) > 11 else ""

                ty.ap(ts, lat_val, lon_val, w, p, st, sn)
            except Exception as e:
                logger.warning(f"解析行失败: {line[:80]} - {e}")
                continue

        if ty.pts:
            if ty.pts[-1]['name']:
                ty.sname = ty.pts[-1]['name']
            ty.start_time = ty.pts[0]['t']
            ty.recalc_simulated_times()
            if add_to_list:
                self.tys.append(ty)
            return ty
        return None

    def _fill_point_categories(self) -> None:
        for ty in self.tys:
            for p in ty.pts:
                if 'cat' not in p:
                    p['cat'] = self.get_strength_category(p['w'], p['st'])

    def _sort_by_basin(self) -> None:
        areas = self.res_mgr.ocean_areas
        basin_order = {a.code: i for i, a in enumerate(areas.areas)} if areas and areas.areas else {}
        repo = self

        def _sort_key(ty: Typhoon) -> tuple:
            basin_idx = basin_order.get(ty.basin, 9999)
            first_time = ty.pts[0]['t'] if ty.pts else "99999999"
            name = repo.get_display_name(ty).lower()
            return (basin_idx, first_time, name)

        self.tys.sort(key=_sort_key)

    def apply_basin_filter(self) -> None:
        backup = self._all_tys_backup
        if not backup:
            self.load_typhoon_files()
            return

        if (getattr(self.cfg, 'basin_filter_enabled', True) and
                self.cfg.ace_limit_mode == "basin" and self.cfg.ace_limit_basin):
            area = self.res_mgr.ocean_areas.get_by_code(self.cfg.ace_limit_basin)
            if area is not None:
                self.tys = [ty for ty in backup if any(
                    area.contains(p['la'], p['lo']) for p in ty.pts)]
            else:
                self.tys = list(backup)
        else:
            self.tys = list(backup)

        self._sort_by_basin()
        if self.cti >= len(self.tys):
            self.cti = 0
        self._refresh_ace()
        self._fill_point_categories()

    def reload_typhoon(self, ty: Typhoon) -> None:
        if not ty.filepath or not os.path.exists(ty.filepath):
            return
        try:
            idx = self.tys.index(ty)
        except ValueError:
            return
        new_ty = self.parse_typhoon_file(ty.filepath, add_to_list=False)
        if new_ty is None:
            return
        self.tys[idx] = new_ty
        if self.edit_typhoon is ty:
            self.edit_typhoon = new_ty
        if self.current_typhoon() is ty:
            self.cti = idx
        self._refresh_ace()
        if self._sim:
            self._sim.view.update_screen_points(self.tys, self.edit_typhoon)
        logger.info(f"台风 {new_ty.name} 已重新加载")

    def reload_typhoons(self) -> None:
        self.tys.clear()
        self.load_typhoon_files()
        self.cti = 0
        self.edit_typhoon = None
        if getattr(self._sim, 'md', 'normal') == "edit" and self.tys:
            self.edit_typhoon = self.tys[0]
        self._refresh_ace()

    def _refresh_ace(self) -> None:
        if self._ace_engine:
            self._ace_engine.refresh_all()

    def recalc_all_ace(self) -> None:
        for ty in self.tys:
            ty.recalc_ace()
        self._refresh_ace()

    def current_typhoon(self) -> Optional[Typhoon]:
        return self.tys[self.cti] if self.tys and 0 <= self.cti < len(self.tys) else None

    def get_display_name(self, ty: Typhoon) -> str:
        if ty.cust:
            return ty.cust
        if ty.sname:
            return ty.sname
        year = ty.start_time[:4] if ty.start_time and len(ty.start_time) >= 4 else ""
        base = f"{ty.basin}{ty.n}" if ty.basin else ty.n
        return f"{base}{year}" if year else base

    @staticmethod
    def _ty_in_filter_basin(ty: Typhoon, area: OceanArea) -> bool:
        pos = ty.cpos()
        if not pos:
            return True
        return area.contains(pos['la'], pos['lo'])

    @staticmethod
    def get_strength_category(wind: int, stype: str) -> str:
        return infer_strength_category(wind, stype)

    gsc = get_strength_category

    @staticmethod
    def darken_color(c: Tuple[int, ...], factor: float = 0.6) -> Tuple[int, ...]:
        return _darken_color(c, factor)

    def get_point_color(self, wind: int, stype: str) -> Tuple[int, int, int]:
        cat = self.get_strength_category(wind, stype)
        if cat == "DB":
            return DB
        elif cat == "EX":
            return EX
        elif cat == "TD":
            return TD
        elif cat == "TS":
            return TS
        elif cat == "STS":
            return STS
        elif cat == "C1":
            return C1
        elif cat == "C2-":
            return C2_MINUS
        elif cat == "C2":
            return C2
        elif cat == "C3-":
            return C3_MINUS
        elif cat == "C3":
            return C3
        elif cat == "C4":
            return C4
        elif cat == "C4-ST":
            return C4_ST
        elif cat == "C5":
            if wind >= 170:
                return C5_D
            elif wind >= 155:
                ratio = (wind - 155) / 15.0
                r = int(C5_L[0] + (C5_D[0] - C5_L[0]) * ratio)
                g = int(C5_L[1] + (C5_D[1] - C5_L[1]) * ratio)
                b = int(C5_L[2] + (C5_D[2] - C5_L[2]) * ratio)
                return (r, g, b)
            else:
                return C5_L
        elif cat == "MD":
            return MD_COLOR
        elif cat == "SD":
            return (100, 150, 200)
        elif cat == "SS":
            return (200, 150, 100)
        elif cat == "LO":
            return (150, 200, 100)
        elif cat == "WV":
            return WV
        return TD
