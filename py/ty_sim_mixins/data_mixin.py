# py/ty_sim_mixins/data_mixin.py
"""数据加载与保存: 台风文件, 配置, ACE 刷新."""
import os
import re
import glob
import json
import csv
import logging
from datetime import datetime
from typing import Optional, List

from ..constants import (
    CONFIG_FILE, TYPHOON_DIR,
    FILE_FORMAT_SIMPLE_BDECK, FILE_FORMAT_JTWC,
)
from ..config import AppConfig
from ..typhoon import Typhoon

logger = logging.getLogger(__name__)


class TySimDataMixin:
    """Mixin: 配置读写、台风文件加载、格式转换、ACE 数据刷新。"""

    # ═══════════════════════════════════════════════
    #  配置读写（委托给 AppConfig）
    # ═══════════════════════════════════════════════
    def load_config(self) -> None:
        """加载配置（已委托给 AppConfig，保留方法名兼容旧调用）。"""
        self.cfg = AppConfig.load(CONFIG_FILE)

    def save_config(self, force: bool = False) -> None:
        """保存配置到文件。"""
        if not force and not self._config_needs_save:
            return
        self.cfg.save(CONFIG_FILE)
        self._config_needs_save = False

    # ═══════════════════════════════════════════════
    #  格式检测与转换
    # ═══════════════════════════════════════════════
    def detect_format(self, filepath: str) -> str:
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
            pass
        return FILE_FORMAT_SIMPLE_BDECK

    def convert_jtwc_to_simple_bdeck(self, input_path: str, output_path: str):
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

    def ensure_simple_bdeck_copy(self, ty: Typhoon):
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

    # ═══════════════════════════════════════════════
    #  台风文件加载
    # ═══════════════════════════════════════════════
    def load_typhoon_files(self) -> None:
        if not os.path.exists(TYPHOON_DIR):
            os.makedirs(TYPHOON_DIR)
            return
        for fp in glob.glob(os.path.join(TYPHOON_DIR, "*.txt")):
            if os.path.isfile(fp):
                self.parse_typhoon_file(fp)
        # 按台风列表的排序逻辑重排 self.tys：
        #   (洋区顺序, 首个报点时间, 名称)
        areas = getattr(getattr(self, 'res_mgr', None), 'ocean_areas', None)
        basin_order = {}
        if areas and areas.areas:
            basin_order = {a.code: i for i, a in enumerate(areas.areas)}
        def _sort_key(ty):
            basin_idx = basin_order.get(ty.basin, 9999)
            first_time = ty.pts[0]['t'] if ty.pts else "99999999"
            name = self.get_display_name(ty).lower()
            return (basin_idx, first_time, name)
        self.tys.sort(key=_sort_key)
        self._refresh_ace_data()
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
                self.show_error(f"无法读取文件 {os.path.basename(fp)}")
                return None

        fmt = self.detect_format(fp)

        fn = os.path.basename(fp)
        m = re.search(r'(\d+)', fn)
        tn = m.group(1) if m else "01"
        ty = Typhoon("WP", tn)
        ty.sim = self
        ty.filepath = fp
        ty.format_type = fmt
        if fmt == FILE_FORMAT_JTWC:
            ty.original_jtwc_source = fp

        # 推测 basin
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

    def _fill_point_categories(self):
        for ty in self.tys:
            for p in ty.pts:
                if 'cat' not in p:
                    p['cat'] = self.get_strength_category(p['w'], p['st'])

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
        self._refresh_ace_data()
        self.update_all_screen_points()
        logger.info(f"台风 {new_ty.name} 已重新加载")

    # ═══════════════════════════════════════════════
    #  ACE 数据刷新（委托给 ACEEngine）
    # ═══════════════════════════════════════════════
    def _refresh_ace_data(self) -> None:
        self.ace_engine.refresh_all()

    def calc_season_years(self) -> None:
        sty, edy = self.ace_engine.season_years()
        self.sty, self.edy = sty, edy
        self.sy = sty

    def calc_yearly_ace(self) -> None:
        self.yad = self.ace_engine.yearly_ace()

    def calc_accumulated_ace_up_to(self, y: int, m: int, d: int, h: int) -> float:
        return self.ace_engine.cumulative_ace_up_to(datetime(y, m, d, h))

    def get_ace_year(self, dt: datetime) -> int:
        return self.ace_engine.ace_year(dt)

    def get_ace_year_start_end(self, year: int) -> tuple:
        return self.ace_engine.ace_year_range(year)

    def _point_in_ace_limit(self, la: float, lo: float) -> bool:
        return self.ace_engine.point_in_limit(la, lo)

    def recalc_all_ace(self) -> None:
        for ty in self.tys:
            ty.recalc_ace()
        self._refresh_ace_data()
        if hasattr(self, 'md') and self.md == self.MODE_SEASON:
            try:
                current_dt = datetime(
                    self.sy,
                    int(self.st[0:2]),
                    int(self.st[2:4]),
                    int(self.st[4:6]),
                )
                self.current_ace_year = self.ace_engine.ace_year(current_dt)
                self.csa = self.ace_engine.cumulative_ace_up_to(current_dt)
            except Exception:
                pass