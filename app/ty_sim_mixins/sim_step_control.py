# app/ty_sim_mixins/sim_step_control.py
"""单步(15min) / 骰子种子 Mixin —— 轮次 158。

两个互相独立的小功能, 只通过 **getattr 探测**读写宿主(TySim)已有的属性与方法:
  ① 单步   : 在**保持暂停**的前提下让物理钟精确前进一个**固定**步长
              (SIM_STEP_ONCE_H = 0.25h = 15min), 供逐帧检视强度/路径演化;
  ② 骰子   : 掷一个 32 位无符号随机种子, 经**既有入口** _sim_set_seed
              (sim_mode_mixin.py:744) 应用, 可选立刻 _sim_reset 让新种子当帧生效。

**为什么"一步"是固定 15min, 而不是复用 step_h(默认 6h)** —— 侦察报告 R7 §a-2/§a-3:
  · 播放的一步 = core.sim_step(sim, step_h, max_inner=1)(sim_mode_mixin.py:1213),
    而 step_h = core.step_hours(sim) 被吸附在 ENV_STEPS = (1.0, 3.0, 6.0)
    (core.py:695 / 739 / 761-769); 实测 snap_env_step(0.25) → 1.0。
    ⇒ **15min 表达不成任何档位**, 只能是 sim_step / _sim_step_core 的一次性 dt。
  · 首选内核私有入口 core._sim_step_core(sim, dt, hold, force_env=False): 它只做
    `sim['t'] += dt`(core.py:4777-4779), 既不写 sim['stepCostMs'] 也不留
    sim['_pendStep']。走公开入口 sim_step(sim, 0.25) 会把 stepCostMs 写成 24 倍
    假值(core.py:4766-4769 的 `_per6 = el * 6/dt`), 而该值正是 _sim_unit_ms
    (sim_mode_mixin.py:1144-1164) 分帧预算的依据 ⇒ 一次单步就会把后续播放拖慢;
    高档位还会连做 inner 次强制环境重建(core.py:4754-4760)。
  · 已知偏差(本轮**不修**内核, 只如实记下): 强度随机强迫(core.py:2702-2703)、
    路径 OU(core.py:3217-3218)、涡旋 OU(core.py:3405-3406)都是"每步一次"、不按
    dt 归一 ⇒ 连按 24 次 15min 的抖动统计会大于"播放一步 6h"。R7 §a-3④ 列了全表。

**显示钟必须一起推进**(否则面板左上角的时钟不跟着走):
  暂停时 _sim_update 只做 `_sim_rate_t_prev = None`(sim_mode_mixin.py:1396-1402),
  渲染/HUD 用的显示钟 _sim_disp_h 归 _sim_advance 维护, 不变式是
  `disp ≤ max(0, t_phys - step_h)`(sim_mode_mixin.py:1197-1199 / 1218-1219)。
  单步沿用**同一条**不变式: 未初始化时按 _sim_advance 的同一规则对齐, 之后按 dt
  推进并单调钳制(只压不回退)。这样按播放时不会出现"日历倒退一步"; 代价是新会话
  上连续单步的前 step_h(默认 6h)面板时钟停在起点 —— 与"刚按播放的头一秒"完全
  一致(同一处首帧初始化), 而台风位置/环境图层每次单步都会更新
  (_sim_display_sim 在 disp ≤ 0 时直接返回物理 sim, sim_mode_mixin.py:1305-1307)。

本文件**不 import TySim**, 也不改任何共享文件; 面板与快捷键由组织者串行落地:
  · 面板: ControlPanel._build_sim_layer_row 里加 'sim_step' / 'sim_dice' 两个
    PanelButton(点击路由走 keyboard_mixin.py:534-536 的 `_btn_<key>` 约定,
    所以**不需要**动 keyboard_mixin 的点击分派);
  · 快捷键: _sim_handle_event(sim_mode_mixin.py:1417-1445) 末尾加一行
    `if self._sim_step_control_key(k): return True`。
"""
from __future__ import annotations

import logging
import random
from typing import Optional

import pygame

logger = logging.getLogger(__name__)

#: 单步的**固定**物理步长(小时) = 15min。见模块 docstring: 它不能进 ENV_STEPS。
SIM_STEP_ONCE_H = 0.25

#: 骰子取值范围: 与 core.init 对种子的归一化一致(core.py:789 `& 0xFFFFFFFF`)。
DICE_SEED_MAX = 0xFFFFFFFF

#: 面板按钮 key(与 control_panel.py 的补丁一致)。点击分派走 _handle_click 的
#: `handler = getattr(self, f"_btn_{btn_key}", None)`(keyboard_mixin.py:534-536)。
BTN_STEP = 'sim_step'
BTN_DICE = 'sim_dice'

#: 键盘: '.' = 单步, ';' = 骰子。两者在模拟模式里都没有被占用 —— 数字 1-9/空格/
#: n/r/d/e/g 已被 _sim_handle_event 吃掉(sim_mode_mixin.py:1422-1443), 通用命令表
#: (input_ctrl.py:24-47)里没有 PERIOD/SEMICOLON, 而 input_ctrl 里的 ,/. 分支只在
#: 编辑模式生效(input_ctrl.py:59-62)。
KEY_STEP = pygame.K_PERIOD
KEY_DICE = pygame.K_SEMICOLON


class TySimStepControlMixin:
    """单步 / 骰子种子(轮次 158)。

    依赖全部经 getattr 探测, 缺了就降级或跳过 —— 因此可以挂在**轻量桩**上单测
    (tests/test_sim_step_control.py), import 期也不需要 TySim。
    """

    # ── 内部工具(一律 getattr 探测) ──

    def _sim_step_control_core(self):
        """取 SimCore 内核模块(core)。测试子类可覆盖本方法注入替身。"""
        try:
            from simulator.simcore import core as V
            return V
        except Exception:
            logger.debug('单步: 取 simcore 内核失败', exc_info=True)
            return None

    def _sim_step_control_notice(self, text: str) -> None:
        """提示走宿主的 _sim_notice(sim_mode_mixin.py:920); 没有就写日志。"""
        fn = getattr(self, '_sim_notice', None)
        if callable(fn):
            try:
                fn(text)
                return
            except Exception:
                logger.debug('单步: 提示失败', exc_info=True)
        logger.info('%s', text)

    @staticmethod
    def _sim_step_control_hold(V, sim) -> int:
        """环境重建间隔倍数 hold = 当前精度档的值(precision_spec 的第 3 项)。

        必须**与播放同一近似**(core.py:4786-4797 按 hold 拉长重建间隔), 否则
        "单步"看到的场比"播放"更新, 两条通路的物理就不一致了。
        """
        spec = getattr(V, 'precision_spec', None)
        if callable(spec):
            try:
                _p = (sim.get('params') or {}).get('precision', '400ms')
                return max(1, int(spec(_p)[2]))
            except Exception:
                logger.debug('单步: precision_spec 失败, hold 退回 1', exc_info=True)
        return 1

    @staticmethod
    def _sim_step_control_step_h(V, sim) -> float:
        """播放步长 step_h(小时, 默认 6h), 只为对齐显示钟的不变式。"""
        fn = getattr(V, 'step_hours', None)
        if callable(fn):
            try:
                return float(fn(sim))
            except Exception:
                logger.debug('单步: step_hours 失败, 退回 envStepH', exc_info=True)
        try:
            return float(sim.get('envStepH', 6.0) or 6.0)
        except Exception:
            return 6.0

    @staticmethod
    def _sim_step_control_t(sim) -> float:
        try:
            return float(sim.get('t', 0.0) or 0.0)
        except Exception:
            return 0.0

    def _sim_step_control_sync_disp(self, V, sim, t1: float, dt: float) -> None:
        """把显示钟按 _sim_advance 的同一不变式往前挪 dt(**单调**, 绝不回退)。

        不变式(sim_mode_mixin.py:1197-1199 / 1218-1219):
            disp ≤ max(0, t_phys - step_h)
        宿主没有 _sim_disp_h(轻量桩)时什么都不做, 不凭空造属性。
        """
        if not hasattr(self, '_sim_disp_h'):
            return
        step_h = self._sim_step_control_step_h(V, sim)
        lim = max(0.0, t1 - step_h)
        if not getattr(self, '_sim_disp_init', False):
            # 与 _sim_advance 首帧同一条规则(否则播放恢复时会看到日历倒退)
            self._sim_disp_h = lim
            self._sim_disp_init = True
            return
        try:
            cur = float(getattr(self, '_sim_disp_h', 0.0) or 0.0)
        except Exception:
            cur = 0.0
        new = min(cur + dt, lim)
        self._sim_disp_h = cur if new < cur else new

    # ── ① 单步 ──

    def _sim_step_once(self) -> bool:
        """保持暂停, 让物理钟**精确**前进 SIM_STEP_ONCE_H(15min)。返回是否推进。

        物理入口优先级(理由见模块 docstring):
          ① core._sim_step_core(sim, dt, hold, force_env=False) —— 首选;
          ② core.sim_step(sim, dt, max_inner=0) —— 兜底(内核没有私有入口时)。
        任一依赖缺失都**降级返回 False**, 不抛异常。
        """
        sim = getattr(self, 'sim_v4', None)
        if not isinstance(sim, dict):
            self._sim_step_control_notice('单步: 模拟未初始化')
            return False
        V = self._sim_step_control_core()
        if V is None:
            self._sim_step_control_notice('单步: 取不到 SimCore 内核')
            return False
        stepper = getattr(V, '_sim_step_core', None)
        use_private = callable(stepper)
        hold = self._sim_step_control_hold(V, sim) if use_private else 1
        if not use_private:
            stepper = getattr(V, 'sim_step', None)
            if not callable(stepper):
                self._sim_step_control_notice('单步: 内核没有 sim_step 入口')
                return False
        # 保持暂停: 单步是逐帧检视用的, 绝不把物理推成"自己在跑"。
        # 只在本就存在该属性时写它(轻量桩不给就不造)。
        if getattr(self, 'sim_playing', None):
            self.sim_playing = False
            sync = getattr(self, '_sim_sync_play_text', None)
            if callable(sync):
                try:
                    sync()
                except Exception:
                    logger.debug('单步: 播放按钮文本同步失败', exc_info=True)
        dt = SIM_STEP_ONCE_H
        t0 = self._sim_step_control_t(sim)
        if use_private:
            stepper(sim, dt, hold, force_env=False)
        else:
            stepper(sim, dt, max_inner=0)
        t1 = self._sim_step_control_t(sim)
        self._sim_step_control_sync_disp(V, sim, t1, dt)
        # 轮次 159: 单步**也要进回放时间轴** —— 否则连按若干次 `.` 再按 `,`, 会跳回
        # 「单步之前」那一份快照, 而单步走过的那些帧既回不去、也没被记录。
        # 用 getattr 探测: 轻量桩/无该 mixin 时安全降级。
        # (实测由 verifier 抓到, VERIFY_R158.md §2.3)
        _tick = getattr(self, '_sim_rewind_tick', None)
        if callable(_tick):
            try:
                _tick()
            except Exception:
                logger.debug('单步: 回放采集失败', exc_info=True)
        self._sim_step_control_notice(
            '单步: +%.0fmin (物理钟 %.2f → %.2fh)' % (dt * 60.0, t0, t1))
        return True

    # ── ② 骰子 ──

    def _sim_dice_seed(self, apply_now: bool = True) -> Optional[int]:
        """掷一个 32 位随机种子并经**既有入口** _sim_set_seed 应用。

        apply_now=True(默认) 时再调 _sim_reset(msg=False), 让新种子当帧生效
        (否则按 _sim_set_seed 的语义要等下次重置, sim_mode_mixin.py:744-760)。
        返回种子; 桩对象缺依赖时同样降级(不抛异常)。
        """
        n = int(random.getrandbits(32)) & DICE_SEED_MAX
        last = getattr(self, '_sim_dice_last', None)
        if last is not None and n == int(last):
            # 骰子的契约是"再掷一次就换一个": 32 位撞车概率 2.3e-10, 但常数 rng
            # 与极端运气都可能撞上 ⇒ 撞了就环形步进一格, 保证必然与上一次不同。
            n = (n + 1) & DICE_SEED_MAX
        self._sim_dice_last = n
        setter = getattr(self, '_sim_set_seed', None)
        applied = False
        if callable(setter):
            try:
                setter(n)
                applied = True
            except Exception:
                logger.debug('骰子: _sim_set_seed 失败, 退回直写', exc_info=True)
        if not applied:
            self.sim_seed = n
            cfg = getattr(self, 'cfg', None)
            if cfg is not None:
                try:
                    cfg.sim_seed = n
                except Exception:
                    logger.debug('骰子: 写 cfg.sim_seed 失败', exc_info=True)
        if apply_now:
            reset = getattr(self, '_sim_reset', None)
            if callable(reset):
                try:
                    reset(msg=False)
                except TypeError:
                    reset()
                except Exception:
                    logger.debug('骰子: 重置失败(种子已记录)', exc_info=True)
            # 覆盖 _sim_set_seed 自己那条"下次重置生效"(我们已经立刻生效了)
            self._sim_step_control_notice('骰子: 种子 %d(已生效)' % n)
        return n

    # ── 面板按钮: 点击路由走 _handle_click 的 `_btn_<key>` 约定 ──

    def _btn_sim_step(self) -> bool:
        self._sim_step_once()
        return True

    def _btn_sim_dice(self) -> bool:
        self._sim_dice_seed()
        return True

    # ── 键盘分派(由 _sim_handle_event 末尾一行接入) ──

    def _sim_step_control_key(self, k: int) -> bool:
        """模拟模式按键: '.' = 单步, ';' = 骰子。返回 True = 已消费。"""
        if k == KEY_STEP:
            return self._sim_step_once()
        if k == KEY_DICE:
            return self._sim_dice_seed() is not None
        return False
