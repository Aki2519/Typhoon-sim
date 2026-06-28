# py/ty_sim_mixins/data_mixin.py
"""数据加载与保存委托。"""
import logging
from datetime import datetime
from typing import Optional

from ..constants import CONFIG_FILE
from ..typhoon import Typhoon
from ..data_repo import DataRepository

logger = logging.getLogger(__name__)


class TySimDataMixin:
    """Mixin: 配置读写、数据加载委托。"""

    def save_config(self, force: bool = False) -> None:
        if not force and not self._config_needs_save:
            return
        self.cfg.save(CONFIG_FILE)
        self._config_needs_save = False

    # ═══════════════════════════════════════════════
    #  委托给 DataRepository
    # ═══════════════════════════════════════════════
    def detect_format(self, filepath: str) -> str:
        return DataRepository.detect_format(filepath)

    def convert_jtwc_to_simple_bdeck(self, input_path, output_path):
        self.repo.convert_jtwc_to_simple_bdeck(input_path, output_path)

    def ensure_simple_bdeck_copy(self, ty):
        self.repo.ensure_simple_bdeck_copy(ty)

    # ═══════════════════════════════════════════════
    #  台风文件加载
    # ═══════════════════════════════════════════════
    def load_typhoon_files(self) -> None:
        self.repo.load_typhoon_files()

    def parse_typhoon_file(self, fp: str, add_to_list: bool = True) -> Optional[Typhoon]:
        return self.repo.parse_typhoon_file(fp, add_to_list)

    def _fill_point_categories(self):
        self.repo._fill_point_categories()

    def _apply_basin_filter(self):
        self.repo.apply_basin_filter()
        self.tys = self.repo.tys
        if hasattr(self, 'season_ctrl'):
            self._sync_to_season_ctrl()

    def reload_typhoon(self, ty: Typhoon) -> None:
        self.repo.reload_typhoon(ty)

    def reload_typhoons(self):
        self.repo.reload_typhoons()
        self.cti = self.repo.cti
        self.edit_typhoon = self.repo.edit_typhoon
        # 刷新后重置风季到最早年份，同步所有状态
        if hasattr(self, 'season_ctrl'):
            sc = self.season_ctrl
            sc.reset_to_first_year()
            self.sty = sc.sty
            self.edy = sc.edy
            self._sync_season_state()
        self.update_all_screen_points()

    # ═══════════════════════════════════════════════
    #  ACE 数据刷新（委托给 ACEEngine）
    # ═══════════════════════════════════════════════
    def _refresh_ace_data(self) -> None:
        self.ace_engine.refresh_all()

    def calc_season_years(self) -> None:
        if hasattr(self, 'season_ctrl'):
            self.season_ctrl.calc_years()
            self.sty = self.season_ctrl.sty
            self.edy = self.season_ctrl.edy
            self.sy = self.season_ctrl.sy

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
        self.repo.recalc_all_ace()
        if hasattr(self, 'md') and self.md == self.MODE_SEASON:
            try:
                current_dt = datetime(
                    self.sy, int(self.st[0:2]), int(self.st[2:4]), int(self.st[4:6]))
                self.current_ace_year = self.ace_engine.ace_year(current_dt)
                self.csa = self.ace_engine.cumulative_ace_up_to(current_dt)
            except Exception:
                logger.debug("recalc_all_ace season reset failed", exc_info=True)
        if hasattr(self, 'season_ctrl'):
            self._sync_to_season_ctrl()