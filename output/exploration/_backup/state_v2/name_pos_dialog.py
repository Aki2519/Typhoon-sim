# py/name_pos_dialog.py
"""「名字位置」可视化调整对话框(用户 2026-10-01, 第二轮投诉后重做)。

## 本轮修的两件事
① **预览必须按地图真实比例**(用户: "现在的名字调整界面没有按正确比例!")。
   地图用的是 `icon_factor = sim._size_factors()[1]`(依赖 `icon_size` /
   `point_size` / `fix_icon_point_size` / 地图缩放), 而旧预览硬编 1.0 ⇒ 用户
   `icon_size=300` 时预览只有真实的 1/3, 根本判断不出"名字离图标多远、有没有压住"。
   现在: 预览取**同一个** `_size_factors()[1]`; 整个场景放不下就**等比缩小**,
   并在界面上写明 `icon_size / point_size / icon_factor / 预览比例`(绝不悄悄换 factor)。
   缩放靠 `draw_typhoon_name(..., zoom=…)`: 它只缩**长度**(图标口径/字号/边距/所有偏移),
   不做位图缩放 ⇒ 缩小后依然锐利。
② **巅峰/登陆标注可独立调整**(用户: "而且不能调整巅峰/登陆!")。
   新增 cfg `peak_dx/peak_dy`(与 `name_dx/name_dy` **同一套语义**: 单位 =
   `icon_factor==1` 时的 px, 渲染时乘 icon_factor; `dy` 正 = 向下), 基准点是
   **名字的左上角** —— 理由: 标注本来就堆在名字下方, 相对名字才能"跟着名字走"
   (调 `name_dx/dy` 时标注一起动), 与用户"名字下面那一行"的心智模型一致;
   若相对图标, 名字长短/字号一变标注就得重调。

## 交互设计(以及理由)
  · **一个"当前调整对象"开关(名字 / 标注)+ 一组方向键**: 面板只有 470px 宽,
    两套方向键(8 颗)+换名字+重置+确定取消会挤成一团、误点率高; 用"当前对象"
    显式切换更贴近用户心理("我现在调的是标注")。
  · **预览里分区拖拽**: 拖名字 = 动名字并自动把当前对象切到"名字"; 拖标注 = 动标注
    (点击即选中), 于是拖拽与方向键**永远作用于同一个对象**, 不会出现"拖的是标注、
    方向键改的是名字"的错觉。
  · **读数分两行** `名字 dx,dy` / `标注 dx,dy`, 当前对象那行高亮。
  · 标注**示例**: 预览里固定显示一条标注示例(当前时刻有没有真实巅峰/登陆都显示),
    因为对话框的用途是调**几何位置**, 固定的示例才能稳定对比; 界面上用
    "标注=示例(非真实数据)"明示, 且它是**画在这里的**, 不写进 `_name_anim`、
    不碰任何真台风状态(沿用上一轮的隔离教训)。

## 隔离(上一轮的教训, 继续保持)
预览走 `draw_typhoon_name(..., name_override=…, label_override=…, force_full=True)`:
`name_override is not None or force_full` ⇒ 该分支**完全不读也不写 `_name_anim`**
(以 ty 对象为键的渲染状态机); 关闭时再防御性清理一次 `_cleanup_anim_state()`。

## 其它
  · `name_dx/name_dy` 的语义("icon_factor=1 时的 px")**没有改**, 只是预览按真实
    factor 渲染; `peak_dx/peak_dy` 与它完全一致 ⇒ 用户不需要学两套。
  · `peak_dx/peak_dy` **不进** `apply_settings` 的 `_sync` 列表(和 `name_dx/name_dy`
    一样): 对话框直接写 cfg(`_ConfigProperty`), 进了 `_sync` 反而会被设置面板里的
    陈旧本地值覆盖回去。因此也不需要往 `_init_data` 加字面量(INV-3 门禁)。
  · 确定 ⇒ 写 cfg + `save_config()`; 取消/ESC/关闭 ⇒ 两组偏移一起回滚。
"""
from __future__ import annotations

import pygame

from .constants import (f_s, f_m, rt, TXT, SETTINGS_TEXT_LIGHT, ICON_SET_SMCY)
from .dialog_base import DraggableDialog
# ⚠ 2026-10-02(W1 修复1R/6c): 原来这里是模块级
#     from .smcy_icon import _TOTAL_FRAMES
#   而 `app/smcy_icon.py:10` 有 `import cv2` ⇒ 本模块被 app/dialog_manager.py
#   **模块级**导入 ⇒ OpenCV 进了启动路径。`_TOTAL_FRAMES` 是纯 int 常量
#   (smcy_icon.py:36 `= 1800`), 唯一使用点在 `_draw_preview()`(**逐帧**),
#   所以既不能留在模块级、也不适合直接写成函数内 import(那会每帧执行一次)。
#   改成"**首次使用时取一次并缓存**": 启动不 import, 之后零额外开销。
_SMCY_TOTAL_FRAMES = None


def _smcy_total_frames() -> int:
    """惰性取 smcy_icon._TOTAL_FRAMES(避免把 cv2 拖进启动路径, 且不逐帧 import)。"""
    global _SMCY_TOTAL_FRAMES
    if _SMCY_TOTAL_FRAMES is None:
        from .smcy_icon import _TOTAL_FRAMES
        _SMCY_TOTAL_FRAMES = int(_TOTAL_FRAMES)
    return _SMCY_TOTAL_FRAMES


from .ty_sim_mixins._draw_icon_mixin import (name_offset_px, _glow_pad,
                                             _LABEL_LINE_GAP_PX, NAME_ANCHOR_IN)

#: 偏移范围(与 `config.py` 的 `_coerce` 同域)
_LIMIT = 300
#: 长按判定与步进
_HOLD_DELAY_MS = 400
_HOLD_STEP_MS = 120
#: 预览用的名字/强度(用户给的: 1978 Rita 170+ / 2013 海燕 170+ / 2026 森拉克 165 /
#: 2014 浣熊 140)。颜色**不写死**, 一律走 `get_point_color(w, 'TY')`。
_PREVIEW_STORMS = (("Rita", 175), ("Haiyan", 175), ("Sinlaku", 165), ("Neoguri", 140))
_PREVIEW_NAMES = frozenset(n for n, _w in _PREVIEW_STORMS)
#: 预览里的**示例**标注(非真实数据, 见 docstring ②)
_EXAMPLE_LABEL = ("Peak 175kt", (255, 214, 90))


class NamePosDialog(DraggableDialog):
    def __init__(self, sim):
        super().__init__(sim)
        self.title = rt(f_m, "名字位置", TXT)
        self.title_dark = rt(f_m, "名字位置", SETTINGS_TEXT_LIGHT)
        self.title_bar_height = 30
        self.dx = 0
        self.dy = 0
        self.pdx = 0
        self.pdy = 0
        self._orig = (0, 0, 0, 0)
        self._committed = False
        self._target = 'name'
        self._hold = None            # [key, t0_ms, next_ms]
        self._drag = False
        self._drag_moved = False
        self._drag_last = (0, 0)
        self._drag_acc = [0.0, 0.0]
        self._drag_base = (0, 0)
        self._name_hit = None        # 名字的屏幕矩形
        self._label_hit = None       # 标注(示例)的屏幕矩形
        self._scratch = None
        self._rot = 0                # 预览名字下标(**只在用户操作时变**)
        self._icon_err = None
        self._icon_src = ''
        self._factor = 1.0           # 预览用的**真实** icon_factor
        self._zoom = 1.0             # 预览等比缩放比(<1 = 放不下, 界面会写明)

    # ── 生命周期 ──────────────────────────────────────────────────────────
    def activate(self, *args, **kwargs):
        super().activate()
        s = self.sim
        self.dx = int(getattr(s, 'name_dx', 0) or 0)
        self.dy = int(getattr(s, 'name_dy', 0) or 0)
        self.pdx = int(getattr(s, 'peak_dx', 0) or 0)
        self.pdy = int(getattr(s, 'peak_dy', 0) or 0)
        self._orig = (self.dx, self.dy, self.pdx, self.pdy)
        self._committed = False
        self._target = 'name'
        self._hold = None
        self._drag = False
        self._drag_moved = False
        self._name_hit = None
        self._label_hit = None
        self._rot = 0                # 每次打开都从第一个名字开始(可预期)
        self._icon_err = None
        self._icon_src = ''
        w, h = 470, 380
        self.bg_rect = pygame.Rect((s.screen_width - w) // 2,
                                   (s.screen_height - h) // 2, w, h)

    def deactivate(self):
        # ⚠ 没点「确定」就退出(ESC / 关闭 / 切模式) ⇒ 两组偏移都回滚
        if not self._committed:
            self._restore()
        self._cleanup_anim_state()
        self._hold = None
        self._drag = False
        super().deactivate()

    def _cleanup_anim_state(self):
        """防御性清理: 万一预览名被写进了真台风的状态机(旧版 bug), 关窗时清掉。

        当前预览路径已**完全隔离**(`name_override` 分支不碰 `_name_anim`),
        正常这里没有东西可清 —— 留着是为了"旧状态残留"也能自愈。
        """
        anim = getattr(self.sim, '_name_anim', None)
        ty = self._pick_ty()
        if not anim or ty is None:
            return
        st = anim.get(ty)
        if isinstance(st, dict) and st.get('name') in _PREVIEW_NAMES:
            anim.pop(ty, None)

    def _restore(self):
        try:
            self.sim.name_dx, self.sim.name_dy = int(self._orig[0]), int(self._orig[1])
            self.sim.peak_dx, self.sim.peak_dy = int(self._orig[2]), int(self._orig[3])
        except Exception:
            pass
        self.dx, self.dy, self.pdx, self.pdy = self._orig

    def _pick_ty(self):
        """挑一个**真实**台风来画图标(名字/标注由预览覆盖, 与它无关)。"""
        s = self.sim
        cands = []
        cur = None
        try:
            cur = s.current_typhoon()
        except Exception:
            cur = None
        if cur is not None:
            cands.append(cur)
        cands.extend(getattr(s, 'tys', None) or [])
        for ty in cands:
            try:
                if ty is not None and getattr(ty, 'pts', None) and ty.cp():
                    return ty
            except Exception:
                continue
        return None

    # ── 几何(全部由 bg_rect 推导 ⇒ 拖动对话框后自动跟着走) ────────────────
    # 纵向排布(自顶向下, 高度 380):
    #   36/52 两行说明 → 74..224 预览 → 230 名字/色块 → 248 尺度与比例
    #   → 270 名字读数 → 288 标注读数 → 306..334 方向键+对象开关
    #   → 346..372 换名字/重置 + 取消/确定
    def _preview_rect(self):
        r = self.bg_rect
        return pygame.Rect(r.x + 18, r.y + 74, r.w - 36, 150)

    def _arrow_rects(self):
        r = self.bg_rect
        y = r.y + 306
        out = {}
        for i, key in enumerate(('left', 'right', 'up', 'down')):
            out[key] = pygame.Rect(r.x + 18 + i * 46, y, 40, 28)
        return out

    def _target_rect(self):
        """「当前调整对象」开关(名字 / 标注)。"""
        r = self.bg_rect
        return pygame.Rect(r.x + 18 + 4 * 46 + 4, r.y + 306, 108, 28)

    def _next_rect(self):
        r = self.bg_rect
        return pygame.Rect(r.x + 18, r.bottom - 34, 84, 26)

    def _reset_rect(self):
        rr = self._next_rect()
        return pygame.Rect(rr.right + 10, rr.y, 84, 26)

    def _ok_rect(self):
        r = self.bg_rect
        return pygame.Rect(r.right - 18 - 84, r.bottom - 34, 84, 26)

    def _cancel_rect(self):
        r = self.bg_rect
        return pygame.Rect(r.right - 18 - 84 - 10 - 84, r.bottom - 34, 84, 26)

    def _readout_ys(self):
        r = self.bg_rect
        return (r.y + 270, r.y + 288)

    # ── 改动 ─────────────────────────────────────────────────────────────
    def _vals(self, target=None):
        tgt = target or self._target
        return (self.dx, self.dy) if tgt == 'name' else (self.pdx, self.pdy)

    def _set_vals(self, target, x, y):
        x = max(-_LIMIT, min(int(x), _LIMIT))
        y = max(-_LIMIT, min(int(y), _LIMIT))
        if target == 'name':
            self.dx, self.dy = x, y
        else:
            self.pdx, self.pdy = x, y

    def _apply(self):
        """把两组偏移都写进 cfg(`_ConfigProperty` ⇒ 直接落到 cfg)。"""
        try:
            self.sim.name_dx, self.sim.name_dy = self.dx, self.dy
            self.sim.peak_dx, self.sim.peak_dy = self.pdx, self.pdy
        except Exception:
            pass

    def _nudge(self, key, step, target=None):
        tgt = target or self._target
        x, y = self._vals(tgt)
        if key == 'left':
            x -= step
        elif key == 'right':
            x += step
        elif key == 'up':
            y -= step
        elif key == 'down':
            y += step
        self._set_vals(tgt, x, y)
        self._apply()

    def _commit(self):
        self._apply()
        try:
            self.sim.save_config()
        except Exception:
            pass
        self._committed = True
        self.deactivate()

    # ── 名字轮换(**只在显式操作时**) ──────────────────────────────────────
    def _storm(self):
        return _PREVIEW_STORMS[self._rot % len(_PREVIEW_STORMS)]

    def _storm_cat(self):
        try:
            return self.sim.get_strength_category(self._storm()[1], 'TY')
        except Exception:
            return 'C5'

    def _storm_color(self):
        try:
            return tuple(self.sim.get_point_color(self._storm()[1], 'TY'))
        except Exception:
            return (240, 240, 245)

    def _rot_next(self):
        self._rot = (self._rot + 1) % len(_PREVIEW_STORMS)

    def _toggle_target(self):
        self._target = 'peak' if self._target == 'name' else 'name'

    # ── 预览: 按**真实**比例布局(场景坐标: 图标中心 = (0,0)) ───────────────
    def real_factor(self):
        """地图当前真正用的 icon_factor(`_size_factors()[1]`)。"""
        try:
            return float(self.sim._size_factors()[1])
        except Exception:
            return 1.0

    def _scene(self, cat, nm, col):
        """按真实 factor 算出场景包围盒与各元素位置(纯估算, 不绘制)。

        只用 mixin 的**公开口径**(`_icon_target_px` / `_get_name_surf` /
        `_get_peak_label_surf` / `_name_core` / `_label_core` / `_glow_pad` /
        `name_offset_px`), 绘制仍走 `draw_typhoon_name` ⇒ 口径唯一, 不会两边漂。
        ⚠ 标注 y 必须与 mixin 里那段**实心文字对齐**的算式**逐字一致**, 否则预览的
          标注框会与实际画出来的标注错位(手感就废了):
            标注可见文字顶边 = 名字可见文字底边 + peak_dy*factor + `_LABEL_LINE_GAP_PX`
        """
        s = self.sim
        f = self.real_factor()
        icon_px = float(s._icon_target_px(cat, f))
        half = icon_px / 2.0
        nf = getattr(s, 'name_size', 100) / 100.0 * 1.5
        pf = getattr(s, 'peak_label_size', 100) / 100.0
        ox, oy = name_offset_px(half, nf, getattr(s, 'name_dx', 0),
                                getattr(s, 'name_dy', 0), f,
                                float(getattr(s, 'name_anchor_in', NAME_ANCHOR_IN)))
        nsurf = s._get_name_surf(nm, col, nf)
        noff = _glow_pad(nf)
        lsurf = s._get_peak_label_surf(_EXAMPLE_LABEL[0], _EXAMPLE_LABEL[1], pf)
        loff = _glow_pad(pf)
        lx = ox - loff + int(getattr(s, 'peak_dx', 0) * f)
        name_rect = pygame.Rect(ox - noff, oy - noff, *nsurf.get_size())
        # 名字表面 blit 在 (ox-noff, oy-noff) ⇒ 名字**实心文字底边**:
        name_bottom = (oy - noff) + s._name_core(nm, col, nf).bottom
        label_core = s._label_core(_EXAMPLE_LABEL[0], _EXAMPLE_LABEL[1], pf)
        ly = (name_bottom + int(getattr(s, 'peak_dy', 0) * f)
              + _LABEL_LINE_GAP_PX - label_core.top)
        label_rect = pygame.Rect(lx, ly, *lsurf.get_size())
        icon_rect = pygame.Rect(-half, -half, int(icon_px), int(icon_px))
        scene = icon_rect.unionall([name_rect, label_rect])
        return dict(f=f, icon_px=icon_px, name_rect=name_rect, label_rect=label_rect,
                    icon_rect=icon_rect, scene=scene)

    # ── 事件 ─────────────────────────────────────────────────────────────
    def handle_event(self, e) -> bool:
        if not self.active:
            return False
        if self.handle_drag_event(e):        # 标题栏拖动
            return True
        if e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.deactivate()            # deactivate 里回滚
                return True
            step = 5 if (int(getattr(e, 'mod', 0) or 0) & pygame.KMOD_SHIFT) else 1
            arrows = {pygame.K_LEFT: 'left', pygame.K_RIGHT: 'right',
                      pygame.K_UP: 'up', pygame.K_DOWN: 'down'}
            if e.key in arrows:
                self._nudge(arrows[e.key], step)
                return True
            if e.key == pygame.K_TAB:
                self._toggle_target()
                return True
            if e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._commit()
                return True
        if e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            # Shift 从**事件自带**的 mod 与实时键盘状态两处取(与 KEYDOWN 分支一致)
            shift = bool((int(getattr(e, 'mod', 0) or 0) | pygame.key.get_mods())
                         & pygame.KMOD_SHIFT)
            for key, rr in self._arrow_rects().items():
                if rr.collidepoint(e.pos):
                    self._nudge(key, 5 if shift else 1)
                    if not shift:
                        self._hold = [key, pygame.time.get_ticks(),
                                      pygame.time.get_ticks() + _HOLD_DELAY_MS]
                    return True
            if self._target_rect().collidepoint(e.pos):
                self._toggle_target()
                return True
            if self._next_rect().collidepoint(e.pos):
                self._rot_next()
                return True
            if self._reset_rect().collidepoint(e.pos):
                self._set_vals(self._target, 0, 0)     # 只重置**当前对象**
                self._apply()
                return True
            if self._ok_rect().collidepoint(e.pos):
                self._commit()
                return True
            if self._cancel_rect().collidepoint(e.pos):
                self.deactivate()
                return True
            # 预览分区拖拽: 拖名字 = 动名字; 拖标注 = 动标注(拖到谁就切到谁)。
            # ⚠ 命中框用的是**实心文字**的框(不是整块带辉光边距的表面): 表面框比文字
            #   大一圈, 在"标注紧贴名字"之后会和标注框重叠 ⇒ 拖标注会被名字抢走。
            #   真重叠(缩得很小)时按"离谁的中心近"判, 避免谁先谁赢的偶然性。
            cands = []
            for rect, tgt in ((self._name_hit, 'name'), (self._label_hit, 'peak')):
                if rect is not None and rect.collidepoint(e.pos):
                    cands.append((abs(e.pos[0] - rect.centerx) + abs(e.pos[1] - rect.centery),
                                  tgt))
            if cands:
                self._target = min(cands)[1]
                return self._begin_drag(e.pos)
            if self.bg_rect.collidepoint(e.pos):
                return True                  # 面板内点击不再穿透到地图
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self._hold = None
            if self._drag and not self._drag_moved:
                self._rot_next()             # 原地单击名字(未拖动) ⇒ 换下一个
            self._drag = False
            return True
        elif e.type == pygame.MOUSEMOTION and self._drag:
            dxp = e.pos[0] - self._drag_last[0]
            dyp = e.pos[1] - self._drag_last[1]
            if dxp or dyp:
                self._drag_moved = True
            self._drag_last = e.pos
            # 屏幕 px -> cfg px: 场景被 factor 放大、又被 zoom 缩过 ⇒ 除 (factor*zoom)
            k = max(1e-6, self._factor * self._zoom)
            self._drag_acc[0] += dxp / k
            self._drag_acc[1] += dyp / k
            self._set_vals(self._target,
                           round(self._drag_base[0] + self._drag_acc[0]),
                           round(self._drag_base[1] + self._drag_acc[1]))
            self._apply()
            return True
        return False

    def _begin_drag(self, pos):
        self._drag = True
        self._drag_moved = False
        self._drag_last = pos
        self._drag_acc = [0.0, 0.0]
        self._drag_base = self._vals(self._target)
        return True

    def _tick_hold(self):
        """长按重复(draw 每帧调): 400ms 后每 120ms ±5。"""
        h = self._hold
        if not h:
            return
        now = pygame.time.get_ticks()
        if now < h[2]:
            return
        self._nudge(h[0], 5)
        h[2] = now + _HOLD_STEP_MS

    # ── 绘制 ─────────────────────────────────────────────────────────────
    def draw(self, surface: pygame.Surface):
        if not self.active:
            return
        self._tick_hold()
        r = self.bg_rect
        dark = self.dark_mode
        if dark:
            self.draw_dark_panel(surface, r)
            self.draw_title(surface, self.title_dark, r, y_offset=10)
        else:
            self.draw_background(surface, r)
            self.draw_title(surface, self.title, r, y_offset=10)
        dim = (168, 178, 196) if dark else (90, 100, 118)
        hot = (255, 240, 120)
        tc = SETTINGS_TEXT_LIGHT if dark else TXT

        # 说明
        surface.blit(rt(f_s, "锚点 = 图标右上角; 单位 = 图标100%时的px", dim),
                     (r.x + 18, r.y + 36))
        surface.blit(rt(f_s, "dx 右+, dy 下+; 拖预览里的名字/标注可直接移动", dim),
                     (r.x + 18, r.y + 52))

        p = self._preview_rect()
        pygame.draw.rect(surface, (20, 26, 38) if dark else (226, 231, 240), p, 0, 6)
        pygame.draw.rect(surface, (70, 84, 108) if dark else (170, 180, 196), p, 1, 6)
        self._name_hit = None
        self._label_hit = None
        nm, wind = self._storm()
        cat = self._storm_cat()
        col = self._storm_color()
        ty = self._pick_ty()
        self._factor = self.real_factor()
        self._zoom = 1.0
        if ty is None:
            surface.blit(rt(f_s, "当前没有可预览的台风(参数仍可调)", dim),
                         (p.x + 10, p.y + 8))
        else:
            try:
                self._draw_preview(surface, p, ty, cat, nm, col)
            except Exception as ex:                      # 预览画不出来要看得见
                self._icon_err = self._icon_err or ("预览绘制失败: %r" % (ex,))
        if self._icon_err:
            surface.blit(rt(f_s, self._icon_err, (255, 110, 110)), (p.x + 10, p.y + 8))

        # 当前预览的名字/强度 + 标注示例说明
        sw = pygame.Rect(p.x + 10, p.bottom + 6, 14, 14)
        pygame.draw.rect(surface, col, sw, 0, 3)
        pygame.draw.rect(surface, (150, 160, 180), sw, 1, 3)
        surface.blit(rt(f_s, "预览: %s  %d kt  (%s)" % (nm, wind, cat), tc),
                     (sw.right + 8, sw.y - 1))
        tgs = rt(f_s, "标注 = 示例(非真实数据)", (255, 190, 120))
        surface.blit(tgs, (r.right - 18 - tgs.get_width(), sw.y - 1))
        # 尺度与预览比例(用户必须知道自己处在什么尺度) —— 同一行左右两段, 都不许超宽
        left = "icon %d / point %d" % (getattr(self.sim, 'icon_size', 100),
                                       getattr(self.sim, 'point_size', 100))
        right = "factor %.2f | 预览 1:%.2f" % (
            self._factor, (1.0 / self._zoom) if self._zoom > 0 else 1.0)
        surface.blit(rt(f_s, left, dim), (r.x + 18, r.y + 248))
        rsz = rt(f_s, right, dim)
        surface.blit(rsz, (r.right - 18 - rsz.get_width(), r.y + 248))

        # 读数**分两行**(当前对象高亮)
        y1, y2 = self._readout_ys()
        for tgt, yy, label in (('name', y1, "名字"), ('peak', y2, "标注")):
            x, y = self._vals(tgt)
            active = (tgt == self._target)
            txt = "%s  dx=%+4d  dy=%+4d%s" % (label, x, y,
                                              "   <- 当前调整" if active else "")
            surface.blit(rt(f_s, txt, hot if active else dim), (r.x + 18, yy))

        # 方向键 + 当前对象开关
        mx, my = pygame.mouse.get_pos()
        labels = {'left': "\u2190", 'right': "\u2192", 'up': "\u2191", 'down': "\u2193"}
        for key, rr in self._arrow_rects().items():
            hov = rr.collidepoint(mx, my)
            if dark:
                self.draw_dark_button(surface, rr, labels[key], hover=hov)
            else:
                self.draw_button(surface, rr, rt(f_m, labels[key], (20, 30, 45)), hover=hov)
        rr = self._target_rect()
        tlabel = "调整: 名字" if self._target == 'name' else "调整: 标注"
        if dark:
            self.draw_dark_button(surface, rr, tlabel, hover=rr.collidepoint(mx, my),
                                  accent=True)
        else:
            self.draw_button(surface, rr, rt(f_m, tlabel, (20, 30, 45)),
                             hover=rr.collidepoint(mx, my))
        # 换名字 / 重置 / 取消 / 确定
        for rect, label, accent in ((self._next_rect(), "换名字", False),
                                    (self._reset_rect(), "重置", False),
                                    (self._cancel_rect(), "取消", False),
                                    (self._ok_rect(), "确定", True)):
            if dark:
                self.draw_dark_button(surface, rect, label,
                                      hover=rect.collidepoint(mx, my), accent=accent)
            else:
                self.draw_button(surface, rect, rt(f_m, label, (20, 30, 45)),
                                 hover=rect.collidepoint(mx, my))

    def _draw_preview(self, surface, p, ty, cat, nm, col):
        """按**地图真实 icon_factor** 绘制预览(放不下就等比缩小并写明比例)。

        做法: 先用 `_scene()` 按真实 factor 估出场景包围盒, 算出 `zoom`; 再把场景
        **按 zoom 绘制**到一张恰好大小的暂存面(图标 + 名字 + 示例标注, 全部走地图
        那条绘制路径), 最后整张贴到预览区 —— 所以预览与地图**严格等比**。
        """
        sc = self._scene(cat, nm, col)
        pw, ph = p.w - 16, p.h - 12
        zoom = min(1.0, pw / max(1.0, sc['scene'].w), ph / max(1.0, sc['scene'].h))
        zoom = max(0.08, zoom * 0.98)
        self._zoom = zoom
        sw = max(8, int(round(sc['scene'].w * zoom)) + 8)
        sh = max(8, int(round(sc['scene'].h * zoom)) + 8)
        if self._scratch is None or self._scratch.get_size() != (sw, sh):
            self._scratch = pygame.Surface((sw, sh), pygame.SRCALPHA)
        self._scratch.fill((0, 0, 0, 0))
        # 场景左上角 -> 暂存面 (4,4); 图标中心随之平移
        icx = int(round(-sc['scene'].x * zoom + 4))
        icy = int(round(-sc['scene'].y * zoom + 4))
        self._icon_err = None
        self._icon_src = ''
        ok, src = self.sim._icon_source_ok(cat)
        self._icon_src = src
        if not ok:
            self._icon_err = "图标资源不可用: %s" % src
            return
        eff = self._factor * zoom
        if getattr(self.sim, 'icon_set', '') == ICON_SET_SMCY:
            fidx = int(pygame.time.get_ticks() / 100) % _smcy_total_frames()
            self.sim._draw_smcy_frame(self._scratch, ty, cat, fidx, icx, icy,
                                      eff, 255, self._storm()[1])
        else:
            self.sim._draw_simple_icon(self._scratch, ty, cat,
                                       {'w': self._storm()[1], 'st': 'TY', 'cat': cat},
                                       icx, icy, eff, 255)
        self.sim.draw_typhoon_name(
            self._scratch, ty, icx, icy, self._factor, 255,
            force_full=True, name_override=nm, color_override=col,
            cat_override=cat, label_override=(_EXAMPLE_LABEL,), zoom=zoom)
        # 命中矩形(场景坐标 -> 屏幕坐标): 拖拽用, 与绘制同一套 zoom
        ox0 = p.centerx - sw / 2.0
        oy0 = p.centery - sh / 2.0
        base_x = ox0 + 4 - sc['scene'].x * zoom
        base_y = oy0 + 4 - sc['scene'].y * zoom
        # 命中矩形: 用**实心文字**(core)的框而不是整块表面(表面含辉光边距, 比文字大
        # 一圈; "标注紧贴名字"之后表面框会互相重叠 ⇒ 拖标注会被名字抢走)。
        ncore = self.sim._name_core(nm, col, getattr(self.sim, 'name_size', 100) / 100.0 * 1.5)
        lcore = self.sim._label_core(_EXAMPLE_LABEL[0], _EXAMPLE_LABEL[1],
                                     getattr(self.sim, 'peak_label_size', 100) / 100.0)

        def _hit(rect, core, pad=3):
            return pygame.Rect(
                int(base_x + (rect.x + core.x) * zoom) - pad,
                int(base_y + (rect.y + core.y) * zoom) - pad,
                max(6, int(core.w * zoom) + 2 * pad),
                max(6, int(core.h * zoom) + 2 * pad))

        self._name_hit = _hit(sc['name_rect'], ncore)
        self._label_hit = _hit(sc['label_rect'], lcore)
        surface.blit(self._scratch, (int(ox0), int(oy0)))
        pygame.draw.rect(surface, (90, 150, 220), self._name_hit, 1, 3)
        pygame.draw.rect(surface, (255, 190, 120), self._label_hit, 1, 3)
