# simulator/env/wrf_fields.py
"""内核A 的 WRF 场提供器: 优先返回 WRF 输出(wrfdir), 域外自动用 real_source 回填。

接口与 FieldAPI 完全一致(get_field/get_param/get_anomaly), 由 run.py 在
kernel='A' 时优先构造; wrfdir 无数据时回落 real_source(数据驱动模式)。
"""
from __future__ import annotations
import json
import os
from collections import OrderedDict
from datetime import date
from typing import Optional

import numpy as np

from . import field_io as F
from . import real_source as RS

WRFDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wrfdir')


class WrfFieldAPI:
    """WRF 输出优先, real_source 补全球。"""

    def __init__(self, wrf_dir: str = WRFDIR, fallback=None):
        self.wrf_dir = wrf_dir
        self.fallback = fallback if fallback is not None else RS.RealFieldAPI()
        self._domain: Optional[dict] = None
        dpath = os.path.join(wrf_dir, 'domain.json')
        if os.path.exists(dpath):
            try:
                with open(dpath, encoding='utf-8') as f:
                    self._domain = json.load(f)
            except Exception:
                self._domain = None
        self._cache: "OrderedDict[str, dict]" = OrderedDict()

    # 逐出最旧天数,限制 npz 整份常驻的内存膨胀(_day 缓存键为日期串)
    _DAY_CACHE_MAX = 16

    def available(self) -> bool:
        """wrfdir 内存在任意 npz。"""
        if not os.path.isdir(self.wrf_dir):
            return False
        return any(fn.endswith('.npz') for fn in os.listdir(self.wrf_dir))

    def load_track(self) -> Optional[list]:
        """WRF 涡旋追踪结果(wrfdir/track.json), 无则 None。"""
        p = os.path.join(self.wrf_dir, 'track.json')
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def _day(self, dt: date) -> dict:
        key = dt.strftime('%Y%m%d')
        if key in self._cache:
            self._cache.move_to_end(key)          # 命中 → 记为最近使用,不重载
            return self._cache[key]
        p = os.path.join(self.wrf_dir, key + '.npz')
        if not os.path.exists(p):
            self._cache[key] = {}
            self._evict_day()
            return {}
        try:
            with np.load(p, allow_pickle=False) as z:
                self._cache[key] = dict(z)
        except Exception:
            self._cache[key] = {}
        self._evict_day()
        return self._cache[key]

    def _evict_day(self) -> None:
        """逐出最旧天数以维持 LRU 上限,防整份 npz 无限常驻(长会话内存膨胀)。"""
        while len(self._cache) > self._DAY_CACHE_MAX:
            self._cache.popitem(last=False)

    @staticmethod
    def _in_domain(dom: Optional[dict], fld: np.ndarray) -> bool:
        return dom is not None and fld is not None and np.isfinite(fld).any()

    def get_field(self, var: str, dt0) -> Optional[np.ndarray]:
        y, mo, d = RS._date_tuple(dt0)
        dt = date(y, mo, d)
        day = self._day(dt)
        fld = day.get(var)
        if fld is None or not np.isfinite(fld).any():
            # 域内无数据 → 回填 real_source
            return self.fallback.get_field(var, dt)
        out = np.array(fld, dtype=np.float32)
        # 域外 NaN → real_source 全球场补缺
        fb = self.fallback.get_field(var, dt)
        if fb is not None:
            if out.ndim == 3:                      # uv_steer
                for i in range(out.shape[0]):
                    bad = ~np.isfinite(out[i])
                    if bad.any():
                        out[i][bad] = fb[i][bad]
            else:
                bad = ~np.isfinite(out)
                if bad.any():
                    out[bad] = fb[bad]
        return out

    def get_anomaly(self, var: str, dt0) -> Optional[np.ndarray]:
        f = self.get_field(var, dt0)
        if f is None:
            return None
        cl = F.load_climatology(var)
        if cl is None or cl['clim'] is None:
            return None
        _, mo, _ = RS._date_tuple(dt0)
        return f - cl['clim'][mo - 1]

    def get_param(self, name: str, dt0) -> Optional[dict]:
        """从 MSLP 场提取副高/ITCZ/季风槽(与 param_fields 口径一致)。"""
        mslp = self.get_field('mslp', dt0)
        if mslp is None:
            return None
        from . import param_fields as PF
        extract = {'ridge': PF._extract_ridge, 'itcz': PF._extract_itcz,
                   'monsoon_trough': PF._extract_trough}.get(name)
        if extract is None:
            return None
        lat, strength, lon_range = extract(mslp)
        return {'lat': lat, 'strength': strength, 'lon_range': lon_range}

    def source_of(self, dt0) -> str:
        y, mo, d = RS._date_tuple(dt0)
        dt = date(y, mo, d)
        return 'wrf' if self._day(dt) else self.fallback.source_of(dt)
