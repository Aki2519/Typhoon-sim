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
from .constants import DB, EX, TD, TS, STS, C1, C2, C3, C4, C5_L, C5_M, C5_D, MD_COLOR, C2_MINUS, C3_MINUS, C4_ST, WV

_C5_SUB_THRESHOLD_MID = 155
_C5_SUB_THRESHOLD_HIGH = 170
_C5_INTERP_RANGE = _C5_SUB_THRESHOLD_HIGH - _C5_SUB_THRESHOLD_MID
_C5_INV_INTERP_RANGE = 1.0 / _C5_INTERP_RANGE

# 经纬度列: 数字 + 方向后缀(如 "250N"/"25.5S"); 缺后缀/小写后缀都归一化
_LATLON_LAT_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*([NSns])$')
_LATLON_LON_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*([EWew])$')

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
    def detect_format(filepath: str, lines: Optional[list] = None) -> str:
        """检测文件格式。lines 非空时直接复用已读取的行,避免重复读文件。"""
        if lines is None:
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
            except Exception:
                return FILE_FORMAT_SIMPLE_BDECK
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(',')
            if len(parts) >= 11:
                col5 = parts[4].strip().upper() if len(parts) > 4 else ''
                if col5 in ('BEST', 'CARQ', 'OFCL', 'OFCI'):
                    return FILE_FORMAT_JTWC
                return FILE_FORMAT_SIMPLE_BDECK
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

    def ensure_simple_bdeck_copy(self, ty: Typhoon) -> bool:
        """把 JTWC 原始文件转换为 simple-bdeck 副本并重指 ty.filepath。

        返回 True 表示 ty.filepath 已指向可写的 simple-bdeck 文件; False 表示
        转换失败(调用方必须放弃保存, 否则会把 simple-bdeck 文本写进原始 JTWC
        文件, 造成原始数据不可逆丢失)。"""
        if ty.format_type != FILE_FORMAT_JTWC or not ty.original_jtwc_source:
            return True
        original_path = ty.original_jtwc_source
        dir_name = os.path.dirname(original_path)
        base_name = os.path.splitext(os.path.basename(original_path))[0]
        new_path = os.path.join(dir_name, f"{base_name}_ty.txt")
        # 0 字节副本同样视为无效: os.path.exists 守卫会让坏副本永久生效
        if not os.path.exists(new_path) or os.path.getsize(new_path) == 0:
            tmp = new_path + '.tmp'
            try:
                self.convert_jtwc_to_simple_bdeck(original_path, tmp)
                if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
                    raise ValueError("转换输出为空")
                os.replace(tmp, new_path)      # 原子替换, 不产生半截文件
            except Exception as e:
                logger.error(f"JTWC 转换失败: {original_path}: {e}")
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except OSError:
                    pass
                return False
        ty.filepath = new_path
        ty.format_type = FILE_FORMAT_SIMPLE_BDECK
        ty.original_jtwc_source = None
        return True

    def load_typhoon_files(self, on_progress=None) -> None:
        self.tys.clear()
        if not os.path.exists(TYPHOON_DIR):
            os.makedirs(TYPHOON_DIR)
            self._all_tys_backup = []   # 目录缺失时不能留下陈旧备份(否则过滤会"复活"台风)
            return
        # 单次目录遍历收集 .txt/.dat(先统计总数以支持启动进度条)
        targets = []
        for root, _dirs, files in os.walk(TYPHOON_DIR):
            derived = {fn[:-len('_ty.txt')].lower() for fn in files
                       if fn.lower().endswith('_ty.txt')}
            for fn in files:
                low = fn.lower()
                if not low.endswith(('.txt', '.dat')):
                    continue
                stem = os.path.splitext(fn)[0].lower()
                if stem in derived and not low.endswith('_ty.txt'):
                    # 原始 JTWC 文件与派生副本并存: 只加载派生副本(*_ty.txt),
                    # 否则同一台风会重复出现, 且编辑内容会被旧原始文件盖掉
                    continue
                targets.append(os.path.join(root, fn))
        total = len(targets)
        for i, fp in enumerate(targets):
            self.parse_typhoon_file(fp)
            if on_progress is not None:
                try:
                    on_progress(i + 1, total)
                except Exception:
                    pass
        self._all_tys_backup = list(self.tys)
        if (getattr(self._sim, 'basin_filter_enabled', True) and
                self.cfg.ace_limit_mode == "basin" and self.cfg.ace_limit_basin):
            area = self.res_mgr.ocean_areas.get_by_code(self.cfg.ace_limit_basin)
            if area is not None:
                # 空 pts 台风(新建占位)豁免过滤,避免重载后丢失
                self.tys = [ty for ty in self.tys if not ty.pts or any(
                    area.contains(p['la'], p['lo']) for p in ty.pts)]
        # N20: 先应用持久化自定义名称(cfg.tn 按 盆域+编号 键)再排序,
        # 使 _sort_by_basin 以最终显示名(cust)作为名称排序键,避免自定义名称
        # 后列表名次与实际显示不一致。
        tn = getattr(self.cfg, 'tn', None) or {}
        for ty in self.tys:
            cust = tn.get(f"{ty.b}{ty.n}")
            if cust:
                ty.cust = cust
        self._sort_by_basin()
        self._refresh_ace()
        self._fill_point_categories()

    def parse_typhoon_file(self, filepath: str, add_to_list: bool = True) -> Optional[Typhoon]:
        encodings = ['utf-8', 'gbk', 'latin-1', 'cp1252']
        lines = None
        for enc in encodings:
            try:
                with open(filepath, 'r', encoding=enc) as f:
                    lines = f.readlines()
                break
            except UnicodeDecodeError:
                continue
            except (IOError, OSError) as e:
                logger.error(f"无法读取文件 {filepath}: {e}")
                if self._sim:
                    self._sim.show_error(f"无法读取文件 {os.path.basename(filepath)}")
                return None
        if lines is None:
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
            except (IOError, OSError) as e:
                logger.error(f"无法读取文件 {filepath}: {e}")
                if self._sim:
                    self._sim.show_error(f"无法读取文件 {os.path.basename(filepath)}")
                return None

        # 复用已读入的 lines 做格式判断,避免同一文件被读两遍
        fmt = self.detect_format(filepath, lines)
        filename = os.path.basename(filepath)
        # 台风编号取文件名中 1~3 位数字段(避免把年份/日期段误当编号,如 "20240801_01")
        # 注意: 两个正则都要带捕获组, 否则命中第一个(如 "...14-16...")时 group(1) 不存在 → IndexError
        m = re.search(r'\b(\d{1,3})\b', filename) or re.search(r'(\d+)', filename)
        tn = m.group(1) if m else "01"
        ty = Typhoon("WP", tn)
        ty.sim = self._sim
        ty.filepath = filepath
        ty.format_type = fmt
        if fmt == FILE_FORMAT_JTWC:
            ty.original_jtwc_source = filepath

        last_basin = None
        new_marker = False
        for line in lines:
            if line.startswith('# NEW'):
                # 新建未加点台风占位头: 恢复 盆域/编号/名称
                new_marker = True
                m2 = re.match(r'# NEW\s+(\S+)\s+(\S+)\s*(.*)', line.strip())
                if m2:
                    basin_code = m2.group(1)
                    num = m2.group(2)
                    # 同步身份字段: b(系统盆域)保持构造值"WP"(全网统一,不可随
                    # 文件内容变化),仅 basin(显示盆域)/n/name 跟随文件身份;否则
                    # b 在 NEW 占位与数据行两种解析路径间不一致,导致 cfg.tn 的自
                    # 定义名称键 f"{b}{n}" 跨重载错位(占位→加点后键从 AL05 变 WP05)。
                    ty.basin = basin_code
                    ty.n = num
                    ty.name = f"{ty.b}{num}"
                    nm = m2.group(3).strip()
                    if nm:
                        ty.cust = ty.sname = nm
                continue
            if not line.strip() or line.startswith('#'):
                continue
            parts = line.split(',')
            if len(parts) >= 1:
                last_basin = parts[0].strip()
            if len(parts) < 11:
                continue
            try:
                ts = parts[2].strip()
                # 第4列(年月日时与 chunshu/agency 之间)为分钟:
                # 非空时拼接到时间串 → t 变为 12 位 YYYYMMDDHHMM
                if len(parts) > 3 and len(ts) == 10 and len(parts[3].strip()) <= 2:
                    mn = parts[3].strip()
                    if mn and mn.isdigit():
                        ts = ts + mn.zfill(2)
                file_number = parts[1].strip()
                if file_number:
                    ty.n = file_number

                if fmt == FILE_FORMAT_JTWC and len(parts) > 5 and parts[5].strip() != '0':
                    continue

                lat_val, lon_val = self._parse_latlon(parts[6], parts[7])
                w = int(parts[8].strip()) if parts[8].strip().isdigit() else 0
                p = int(parts[9].strip()) if parts[9].strip().isdigit() else 0
                st = parts[10].strip()

                if fmt == FILE_FORMAT_JTWC:
                    sn = parts[27].strip() if len(parts) > 27 else ""
                else:
                    sn = parts[11].strip() if len(parts) > 11 else ""

                ty.ap(ts, lat_val, lon_val, w, p, st, sn)
            except Exception as e:
                logger.warning(f"解析行失败: {line[:80]} - {e}")
                continue

        if last_basin:
            ty.basin = last_basin

        # 解析过程中 ty.n 被每条数据行的第2列覆盖(如 "2026 D5W wpD52026" → n="D5"),
        # 此时构造时生成的 ty.name(=f"{b}{文件名编号}") 已过期,按最终身份刷新。
        if ty.n and ty.b:
            ty.name = f"{ty.b}{ty.n}"

        if ty.pts:
            if ty.pts[-1]['name']:
                ty.sname = ty.pts[-1]['name']
            ty.start_time = ty.pts[0]['t']
            ty.recalc_simulated_times()
            if add_to_list:
                self.tys.append(ty)
            return ty
        if new_marker:
            # 新建未加点台风: 保留对象(重载后不消失),无 pts 不参与播放
            ty.start_time = ty.start_time or "2000010100"
            ty.recalc_simulated_times()
            if add_to_list:
                self.tys.append(ty)
            return ty
        return None

    @staticmethod
    def _parse_latlon(lat_str_raw: str, lon_str_raw: str):
        """解析 NSEW 经纬度列(空列/缺方向后缀抛 ValueError 由调用方跳过整行)。"""
        lat_str = lat_str_raw.strip()
        lon_str = lon_str_raw.strip()
        mlat = _LATLON_LAT_RE.match(lat_str)
        mlon = _LATLON_LON_RE.match(lon_str)
        if mlat is None or mlon is None:
            # 缺方向后缀/非法格式: 按 docstring 抛错, 由调用方跳过整行,
            # 而不是静默砍掉末位造成 "25"→2.5 这类错位解析
            raise ValueError("经纬度列缺少方向后缀或格式非法")
        lat_val = float(mlat.group(1)) / 10.0
        if mlat.group(2).upper() == 'S':
            lat_val = -lat_val
        lon_val = float(mlon.group(1)) / 10.0
        if mlon.group(2).upper() == 'W':
            lon_val = 360.0 - lon_val
            if lon_val >= 360.0:
                lon_val = 0.0      # 0W 与 0E 等价
        return lat_val, lon_val

    def _fill_point_categories(self) -> None:
        for ty in self.tys:
            for p in ty.pts:
                if not p.get('cat'):
                    p['cat'] = self.get_strength_category(p.get('w', 0), p.get('st', ''))

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

        # 记住当前选中的台风身份，以免重排序/过滤后丢失选中
        saved_cti_id = None
        if self.cti is not None and 0 <= self.cti < len(self.tys):
            t = self.tys[self.cti]
            saved_cti_id = (t.basin, t.n, t.filepath)
        saved_edit_id = None
        if self.edit_typhoon:
            saved_edit_id = (self.edit_typhoon.basin, self.edit_typhoon.n, self.edit_typhoon.filepath)

        if (getattr(self._sim, 'basin_filter_enabled', True) and
                self.cfg.ace_limit_mode == "basin" and self.cfg.ace_limit_basin):
            area = self.res_mgr.ocean_areas.get_by_code(self.cfg.ace_limit_basin)
            if area is not None:
                # N15: 空 pts 台风(新建未加点)豁免过滤,避免从列表消失
                self.tys = [ty for ty in backup if not ty.pts or any(
                    area.contains(p['la'], p['lo']) for p in ty.pts)]
            else:
                self.tys = list(backup)
        else:
            self.tys = list(backup)

        self._sort_by_basin()

        # 恢复到排序后的正确位置
        if saved_cti_id:
            found = False
            for i, ty in enumerate(self.tys):
                if (ty.basin, ty.n, ty.filepath) == saved_cti_id:
                    self.cti = i
                    found = True
                    break
            if not found:
                self.cti = 0
        if not (0 <= self.cti < len(self.tys)):
            self.cti = 0

        if saved_edit_id:
            found = False
            for ty in self.tys:
                if (ty.basin, ty.n, ty.filepath) == saved_edit_id:
                    self.edit_typhoon = ty
                    found = True
                    break
            if not found:
                self.edit_typhoon = None

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
        if self._all_tys_backup:
            fp = new_ty.filepath
            for i, bt in enumerate(self._all_tys_backup):
                if bt.filepath == fp:
                    self._all_tys_backup[i] = new_ty
                    break
        if self.edit_typhoon is ty:
            self.edit_typhoon = new_ty
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
        if self._sim:
            return self._sim.get_display_name(ty)
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

    @staticmethod
    def darken_color(c: Tuple[int, ...], factor: float = 0.6) -> Tuple[int, ...]:
        return _darken_color(c, factor)

    _COLOR_MAP = {
        "DB": DB, "EX": EX, "TD": TD, "TS": TS, "STS": STS,
        "C1": C1, "C2-": C2_MINUS, "C2": C2,
        "C3-": C3_MINUS, "C3": C3, "C4": C4, "C4-ST": C4_ST,
        "MD": MD_COLOR, "SD": (100, 150, 200),
        "SS": (200, 150, 100), "LO": (150, 200, 100), "WV": WV,
    }

    def get_point_color(self, wind: int, stype: str) -> Tuple[int, int, int]:
        cat = self.get_strength_category(wind, stype)
        if cat == "C5":
            if wind >= _C5_SUB_THRESHOLD_HIGH:
                return C5_D
            elif wind >= _C5_SUB_THRESHOLD_MID:
                ratio = (wind - _C5_SUB_THRESHOLD_MID) * _C5_INV_INTERP_RANGE
                r = int(C5_M[0] + (C5_D[0] - C5_M[0]) * ratio)
                g = int(C5_M[1] + (C5_D[1] - C5_M[1]) * ratio)
                b = int(C5_M[2] + (C5_D[2] - C5_M[2]) * ratio)
                return (r, g, b)
            return C5_L
        return self._COLOR_MAP.get(cat, TD)
