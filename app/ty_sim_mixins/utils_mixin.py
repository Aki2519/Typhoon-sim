# py/ty_sim_mixins/utils_mixin.py
"""工具方法：坐标转换、编辑操作、错误提示。"""
from __future__ import annotations
import os
import pygame
import logging
from datetime import datetime, timedelta
from ..typhoon import TrackPoint
from ..typhoon_render import _clear_geo_spline_cache
from ..constants import FILE_FORMAT_JTWC

logger = logging.getLogger(__name__)


class TySimUtilsMixin:
    """工具方法：坐标转换委托、编辑操作、错误提示。"""

    def darken_color(self, c, factor=0.6):
        return self.repo.darken_color(c, factor)

    def latlon_to_screen(self, la, lo):
        return self.view.latlon_to_screen(la, lo)

    def screen_to_latlon(self, x, y):
        return self.view.screen_to_latlon(x, y)

    def get_strength_category(self, wind, stype):
        return self.repo.get_strength_category(wind, stype)

    gsc = get_strength_category

    def get_point_color(self, wind, stype):
        return self.repo.get_point_color(wind, stype)

    def tint_image(self, image, color):
        tinted = pygame.Surface(image.get_size(), pygame.SRCALPHA)
        tinted.fill((*color, 0))
        tinted.blit(image, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
        return tinted

    def show_error(self, message: str) -> None:
        self.show_toast(message, 'error')

    def show_toast(self, text: str, level: str = 'info', duration_ms: int = 2500) -> None:
        """轻量 toast: 顶部居中堆叠(最多 3 条), level 区分颜色。
        info/success/warning/error → 蓝/绿/黄/红。"""
        if not hasattr(self, '_toasts'):
            self._toasts = []
        self._toasts.append((text, level, pygame.time.get_ticks() + duration_ms))
        if len(self._toasts) > 3:
            self._toasts = self._toasts[-3:]

    def get_next_time_for_typhoon(self, ty) -> str:
        if not ty.start_time:
            if ty.pts and ty.pts[0].get('t'):
                ty.start_time = ty.pts[0]['t']
            else:
                return "2000010100"
        try:
            if len(ty.start_time) >= 12:
                base = datetime.strptime(ty.start_time[:12], "%Y%m%d%H%M")
                fmt = "%Y%m%d%H%M"
            else:
                base = datetime.strptime(ty.start_time[:10], "%Y%m%d%H")
                fmt = "%Y%m%d%H"
            new_time = base + timedelta(hours=6 * len(ty.pts))
            return new_time.strftime(fmt)
        except ValueError:
            return "2000010100"

    def _reposition_typhoon_on_path(self, ty) -> None:
        """点数据变化后，把台风当前显示位置(ipos)重定位到新路径上。
        否则旧 ipos 残留会与移动后的首点之间连出伪线（live segment）。"""
        n = len(ty.pts)
        if not n or ty.ci < 0 or ty.ci >= n:
            return
        if ty.v.ipos is None:
            return
        pt_list = ty.points_time
        if len(pt_list) != n or ty.ci + 1 >= n:
            ty.v.ipos = None
            return
        pt0, pt1 = pt_list[ty.ci], pt_list[ty.ci + 1]
        total = pt1 - pt0
        t = (ty.at - pt0) / total if total > 0 else 0.0
        t = min(1.0, max(0.0, t))
        if t <= 0:
            # at 未离开段起点：台风应停在新 pts[ci] 上
            ty.v.ipos = None
            return
        use_smooth = (self.cfg.smooth_path and ty.v.smooth_screen_points
                      and ty.v._smooth_arc_lengths)
        if use_smooth:
            from ..spline import position_at_arc
            segs = max(1, self.cfg.smooth_path_segments)
            arcs = ty.v._smooth_arc_lengths
            i0 = ty.ci * segs
            if i0 >= len(arcs):
                i0 = len(arcs) - 1     # 与 typhoon_sim._move_on_curve 同款钳制
            i1 = min((ty.ci + 1) * segs, len(arcs) - 1)
            if i1 <= i0:
                i1 = i0 + 1 if i0 + 1 < len(arcs) else i0
            seg_start = arcs[i0]
            seg_total = arcs[i1] - seg_start
            if seg_total <= 0:
                seg_total = 1.0
            sc_x, sc_y = position_at_arc(
                ty.v.smooth_screen_points, arcs, seg_start + seg_total * t)
            la, lo = self.screen_to_latlon(sc_x, sc_y)
            ty.v.ipos['la'] = la
            ty.v.ipos['lo'] = lo
        else:
            cp, np = ty.pts[ty.ci], ty.pts[ty.ci + 1]
            ty.v.ipos['la'] = cp['la'] + (np['la'] - cp['la']) * t
            ty.v.ipos['lo'] = cp['lo'] + (np['lo'] - cp['lo']) * t

    def refresh_typhoon_after_point_change(self, ty) -> None:
        self._invalidate_path_cache_for_ty(ty)
        ty._cached_landfalls = None
        ty._ace_year_events = None
        ty._ace_ty_yearly = None
        ty._officials = None   # 微优化1: RI 官方报索引缓存失效
        ty._ipol_key = None    # 法20 插值缓存: 点数据变更必须失效
        ty.recalc_simulated_times()
        self._reposition_typhoon_on_path(ty)

    def _drop_season_start_cache(self, ty) -> None:
        if hasattr(self, 'season_ctrl'):
            self.season_ctrl._start_cache.pop(ty, None)

    def _delete_edit_point(self, idx, ty=None) -> bool:
        """编辑模式删除报点(带撤销)。同步地图选中与报点列表状态。"""
        ty = ty or self.edit_typhoon
        if not ty or not ty.pts or idx is None or not (0 <= idx < len(ty.pts)):
            return False
        ty.push_snapshot()
        del ty.pts[idx]
        n = len(ty.pts)
        new_idx = min(idx, n - 1) if n else None
        self._edit_selected_point = new_idx
        self._last_edited_point = new_idx
        ty.recalc_ace()
        _clear_geo_spline_cache(ty)
        ty.update_screen_points(self.latlon_to_screen)
        self.refresh_typhoon_after_point_change(ty)
        self._refresh_ace_data(ty)
        self._season_info_box_cache.pop(ty, None)
        self._season_info_box_last_data.pop(ty, None)
        pl = self.dialog_mgr.point_list
        if pl.active:
            pl._clear_row_cache()
            pl._needs_save = True
            pl.current_page = min(pl.current_page, max(0, pl.get_total_pages() - 1))
            pl.selected_index = new_idx if new_idx is not None else -1
        else:
            pl.save_typhoon_to_file(ty)
        self._drop_season_start_cache(ty)
        return True

    def _select_edit_point(self, delta: int) -> bool:
        """编辑模式: 切换选中报点(循环)。"""
        ty = self.edit_typhoon
        if not ty or not ty.pts:
            return False
        n = len(ty.pts)
        sel = getattr(self, '_edit_selected_point', None)
        if sel is None or not (0 <= sel < n):
            sel = 0
        else:
            sel = (sel + delta) % n
        self._edit_selected_point = sel
        self._last_edited_point = sel
        pl = self.dialog_mgr.point_list
        if pl.active:
            pl.selected_index = sel
            pl.current_page = sel // pl.rows_per_page
            pl._clear_row_cache()
        return True

    def _point_edit_nav(self, cur: int, delta: int):
        """编辑报点对话框按 [ ] 切换上一/下一个报点。

        返回 (init_values, callback, new_index); 越界/无编辑台风返回 None。
        同时把地图选中与报点列表选中同步到新索引。"""
        ty = self.edit_typhoon
        if not ty or not ty.pts:
            return None
        idx = cur + delta
        if not (0 <= idx < len(ty.pts)):
            return None
        pt = ty.pts[idx]
        init = {
            'wind': str(pt.get('w', '')),
            'pressure': str(pt.get('p', '')),
            'type': str(pt.get('st', '')),
            'lat': str(pt.get('la', '')),
            'lon': str(pt.get('lo', '')),
            'time': str(pt.get('t', '')),
        }
        cb = lambda vals, idx=idx: self.update_point_in_edit_typhoon(vals, idx)
        self._edit_selected_point = idx
        self._last_edited_point = idx
        pl = self.dialog_mgr.point_list
        if pl.active:
            pl.selected_index = idx
            pl.current_page = idx // pl.rows_per_page
            pl._clear_row_cache()
        return init, cb, idx

    def open_point_edit_for_selected(self) -> bool:
        """编辑模式: 为当前选中报点弹出编辑报点对话框(Enter 快捷键)。

        与长按报点一致: 预填该报点值、提交回调绑定该报点索引,
        并支持 , / . 在对话框内继续切换相邻报点。"""
        ty = self.edit_typhoon
        sel = getattr(self, '_edit_selected_point', None)
        if not ty or sel is None or not (0 <= sel < len(ty.pts)):
            return False
        pt = ty.pts[sel]
        init = {
            'wind': str(pt.get('w', '')),
            'pressure': str(pt.get('p', '')),
            'type': str(pt.get('st', '')),
            'lat': str(pt.get('la', '')),
            'lon': str(pt.get('lo', '')),
            'time': str(pt.get('t', '')),
        }
        self.dialog_mgr.point_edit_dialog.activate(
            init,
            lambda vals, idx=sel: self.update_point_in_edit_typhoon(vals, idx),
            point_nav=self._point_edit_nav,
            point_index=sel,
            point_total=len(ty.pts),
        )
        self._last_edited_point = sel
        return True

    def delete_typhoon(self, ty) -> bool:
        """删除台风(含磁盘文件): 列表/备份移除、选中态修正、全链路缓存清理。"""
        if ty not in self.tys:
            return False
        self.tys.remove(ty)
        backup = self.repo._all_tys_backup
        # 备份残留会导致盆域过滤(apply_basin_filter)后"复活"已删除台风
        if backup is not None and ty in backup:
            backup.remove(ty)
        # 选中态修正
        if self.edit_typhoon is ty:
            self.edit_typhoon = self.tys[0] if self.tys else None
            self._edit_selected_point = None
            self._last_edited_point = None
        if self.cti >= len(self.tys):
            self.cti = max(0, len(self.tys) - 1)
        # 关闭引用该台风的活动对话框
        pl = self.dialog_mgr.point_list
        if pl.active and pl.typhoon is ty:
            pl.deactivate()
        ic = self.dialog_mgr.intensity_chart
        if ic.active and getattr(ic, '_typhoon', None) is ty:
            ic.deactivate()
        # 统计图表持有台风对象引用的也关闭,避免陈旧数据/越界
        for dlg in (self.dialog_mgr.path_comparison, self.dialog_mgr.intensity_comparison):
            if dlg.active and any(t is ty for t in getattr(dlg, '_tys', None) or []):
                dlg.deactivate()
        # 季节信息框 slot 释放(占用槽位不归还会导致新台风无框可显示)
        slot = self.info_box_slots.pop(ty, None)
        if slot is not None and slot not in self.info_box_free_slots:
            self.info_box_free_slots.append(slot)
            self.info_box_free_slots.sort()
        anim = getattr(self, '_box_anim', None)
        if anim:
            anim.pop(ty, None)
        # 按台风键控的绘制/信息框缓存清理
        for c in ('_info_box_cache_typhoon', '_info_box_last_data',
                  '_season_info_box_cache', '_season_info_box_last_data'):
            d = getattr(self, c, None)
            if d:
                d.pop(ty, None)
        self._drop_season_start_cache(ty)
        self._invalidate_path_cache_for_ty(ty)
        if hasattr(self, '_name_anim'):
            self._name_anim.pop(ty, None)
        try:
            from ..typhoon_render import _clear_geo_spline_cache
            _clear_geo_spline_cache(ty)
        except Exception:
            pass
        # 播放控制器缓存(特效/登陆检测/结束标记)
        pc = self.playback_ctrl
        pc._was_fin.pop(ty, None)
        pc._lf_last.pop(ty, None)
        if pc.effects:
            pc.effects = [e for e in pc.effects
                          if getattr(e, 'ty', None) is not ty
                          and getattr(e, 'typhoon', None) is not ty]
        if self.effects:
            self.effects = [e for e in self.effects
                            if getattr(e, 'ty', None) is not ty
                            and getattr(e, 'typhoon', None) is not ty]
        try:
            from .. import summary_effect as _se
            for _s, _v in list(_se._slot_registry.items()):
                if getattr(_v, 'ty', None) is ty:
                    _se._slot_registry.pop(_s, None)
            _se._wait_queue[:] = [v for v in _se._wait_queue
                                  if getattr(v, 'ty', None) is not ty]
        except Exception:
            pass
        # 镜头跟踪状态: 删除台风后必须清引用, 否则相机被锁在已删除台风的位置,
        # 且 _tracking_seen 永久持有已删除对象(强引用泄漏)
        seen = getattr(self, '_tracking_seen', None)
        if seen is not None:
            seen.discard(ty)
        if getattr(self, 'tracking_typhoon', None) is ty:
            self.tracking_typhoon = None
        if getattr(self, '_tracked_ty', None) is ty:
            self._tracked_ty = None
        # ACE 数据全量刷新(删除后重算总计/年份/图表缓存)
        self._refresh_ace_data()
        self.update_all_screen_points()
        self._sync_to_season_ctrl()
        # 删除磁盘文件(失败仅提示,不阻塞);
        # JTWC 台风若已转换过,派生 _ty.txt 副本一并删除,避免重载后残留复活
        paths = [ty.filepath]
        if (getattr(ty, 'format_type', '') == FILE_FORMAT_JTWC and ty.filepath):
            base = os.path.splitext(os.path.basename(ty.filepath))[0]
            paths.append(os.path.join(os.path.dirname(ty.filepath), f"{base}_ty.txt"))
        for p in paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError as e:
                    self.show_error(f"删除文件失败: {e}")
        return True

    def _nudge_edit_point(self, dx: int, dy: int) -> bool:
        """编辑模式: 按吸附步长微调选中报点(Ctrl+方向键)。
        dx>0 东移, dy>0 北移。"""
        ty = self.edit_typhoon
        sel = getattr(self, '_edit_selected_point', None)
        if not ty or sel is None or not (0 <= sel < len(ty.pts)):
            return False
        step = getattr(self.cfg, 'edit_snap_step', 0.1)
        if not step or step <= 0:
            step = 0.1
        pt = ty.pts[sel]
        new_la = max(-90.0, min(90.0, pt['la'] + dy * step))
        new_lo = max(0.0, min(360.0, pt['lo'] + dx * step))
        if new_la == pt['la'] and new_lo == pt['lo']:
            return False
        ty.push_snapshot()      # 与其它编辑路径一致: 微调必须可撤销
        pt['la'] = new_la
        pt['lo'] = new_lo
        from ..typhoon_render import _clear_geo_spline_cache
        _clear_geo_spline_cache(ty)
        ty.update_screen_points(self.latlon_to_screen)
        # 与其它编辑操作一致：微调跨过 ACE 地理限制边界时重算 ACE，
        # 并失效 ACE/季节信息框缓存（否则显示的 ACE/信息框残留旧值）
        ty.recalc_ace()
        self.refresh_typhoon_after_point_change(ty)
        self._refresh_ace_data(ty)
        self._season_info_box_cache.pop(ty, None)
        self._season_info_box_last_data.pop(ty, None)
        pl = self.dialog_mgr.point_list
        if pl.active:
            pl._clear_row_cache()
            pl._needs_save = True
        else:
            pl.save_typhoon_to_file(ty)
        return True

    def add_point_to_edit_typhoon(self, vals: dict, current_name: str) -> None:
        ty = self.edit_typhoon
        if not ty:
            return
        try:
            la = float(vals['lat'])
            lo = float(vals['lon'])
            if la < -90 or la > 90:
                self.show_error("纬度必须在 -90 到 90 之间")
                return
            if lo < 0 or lo > 360:
                self.show_error("经度必须在 0 到 360 之间")
                return
        except ValueError:
            self.show_error("经纬度必须是有效数字")
            return

        try:
            # 先解析校验,全部通过后再快照,避免解析失败残留无效果撤销步
            w = int(vals['wind']) if vals['wind'] else 15
            p = int(vals['pressure']) if vals['pressure'] else 0
            if w < 0 or p < 0:
                self.show_error("风速/气压不能为负数")
                return
            st = (vals['type'] or '').strip() or self.dialog_mgr.point_list._infer_type(w, ty.basin)
            t = vals['time']

            ty.push_snapshot()
            cat = self.get_strength_category(w, st)
            color = self.get_point_color(w, st)
            color_dim = self.darken_color(color, 0.6)

            ace_year = 0
            if len(t) >= 10:
                try:
                    dt = datetime.strptime(t[:10], "%Y%m%d%H")
                    ace_year = self.get_ace_year(dt)
                except Exception:
                    ace_year = 0

            if len(ty.pts) == 0 and la < 0:
                ty.mirror = True
                ty.rot_dir = -1
            # 自动检测洋区
            if len(ty.pts) == 0 and self.res_mgr.ocean_areas.areas:
                area = self.res_mgr.ocean_areas.find_area(la, lo)
                if area:
                    ty.basin = area.code

            new_point = TrackPoint(
                t=t, la=la, lo=lo, w=w, p=p, st=st,
                cat=cat, color=color, color_dim=color_dim,
                name=current_name, official=True,
                ace=0, pace=0, ace_year=ace_year,
            )

            # 按时间顺序插入,保证路径按时间连接(无论输入时间在何时)
            new_idx = ty.insert_point_by_time(new_point)
            ty.recalc_ace()
            # N7: 先清 geo 样条缓存,再重投影(就地修改 pts 时旧样条不得复用)
            _clear_geo_spline_cache(ty)
            ty.update_screen_points(self.latlon_to_screen)
            self.refresh_typhoon_after_point_change(ty)
            self._refresh_ace_data(ty)
            self._season_info_box_cache.pop(ty, None)
            self._season_info_box_last_data.pop(ty, None)
            self.dialog_mgr.point_list.save_typhoon_to_file(ty)

            self._drop_season_start_cache(ty)

            # 与 update/delete 一致: 新增点自动成为地图选中点(避免
            # _last_edited_point 指向新点而 _edit_selected_point 残留旧 idx 的错位)
            self._last_edited_point = new_idx
            self._edit_selected_point = new_idx

        except ValueError:
            self.show_error("添加点失败：数值格式错误")

    def update_point_in_edit_typhoon(self, vals: dict, point_index: int) -> None:
        """编辑模式：更新台风已有报点。"""
        ty = self.edit_typhoon
        if not ty or point_index < 0 or point_index >= len(ty.pts):
            return
        try:
            la = float(vals['lat'])
            lo = float(vals['lon'])
            if la < -90 or la > 90:
                self.show_error("纬度必须在 -90 到 90 之间")
                return
            if lo < 0 or lo > 360:
                self.show_error("经度必须在 0 到 360 之间")
                return
        except ValueError:
            self.show_error("经纬度必须是有效数字")
            return

        try:
            w = int(vals['wind']) if vals['wind'] else 15
            p = int(vals['pressure']) if vals['pressure'] else 0
            if w < 0 or p < 0:
                self.show_error("风速/气压不能为负数")
                return
            st = (vals['type'] or '').strip() or ty.pts[point_index].get('st', '')
            t = vals['time']
            ty.push_snapshot()

            cat = self.get_strength_category(w, st)
            color = self.get_point_color(w, st)
            color_dim = self.darken_color(color, 0.6)

            ace_year = 0
            if len(t) >= 10:
                try:
                    dt = datetime.strptime(t[:10], "%Y%m%d%H")
                    ace_year = self.get_ace_year(dt)
                except (ValueError, Exception):
                    ace_year = 0

            pt = ty.pts[point_index]
            pt['t'] = t
            pt['la'] = la
            pt['lo'] = lo
            pt['w'] = w
            pt['p'] = p
            pt['st'] = st
            pt['cat'] = cat
            pt['color'] = color
            pt['color_dim'] = color_dim
            pt['ace_year'] = ace_year

            # 时间被修改后按时间重排,保证路径按时间连接
            point_index = ty.resort_point_by_time(point_index)
            ty.recalc_ace()
            # N7: 先清 geo 样条缓存,再重投影
            _clear_geo_spline_cache(ty)
            ty.update_screen_points(self.latlon_to_screen)
            self.refresh_typhoon_after_point_change(ty)
            self._refresh_ace_data(ty)
            self._season_info_box_cache.pop(ty, None)
            self._season_info_box_last_data.pop(ty, None)
            self.dialog_mgr.point_list.save_typhoon_to_file(ty)

            self._drop_season_start_cache(ty)

            self._last_edited_point = point_index
            # 时间被重排后原选中索引可能指向了别的报点(或越界)：
            # 把地图选中/报点列表选中同步到重排后的新索引,避免删错/微调错点
            self._edit_selected_point = point_index
        except ValueError:
            self.show_error("修改点失败：数值格式错误")
        except Exception as e:
            logger.debug(f"修改点失败: {e}", exc_info=True)
            self.show_error("修改点失败")