"""颜色常量：台风强度色、UI色、设置暗色主题、图表色"""
BG = (230, 245, 255)
GRID = (200, 220, 240)
TXT = (20, 40, 80)
PATH = (70, 130, 180)
CUR_POS = (220, 60, 60)
EDIT = (255, 0, 0, 180)
SPEC = (180, 20, 20)
LIST_BG = (240, 248, 255, 220)
LIST_HL = (150, 200, 240, 150)
DB = (128, 128, 128)
EX = (173, 216, 230)
TD = (0, 96, 255)
TS = (14, 209, 69)
STS = (0, 255, 0)
C1 = (255, 255, 0)
C2 = (255, 176, 0)
C3 = (255, 96, 0)
C4 = (255, 0, 0)
C5_L = (255, 0, 255)
C5_M = (191, 0, 255)
C5_D = (128, 0, 255)
MD_COLOR = STS   # 与 STS 同色,避免两处维护漂移
C2_MINUS = (255, 212, 0)
C3_MINUS = (255, 136, 0)
C4_ST = (255, 0, 191)
WV = (0, 191, 255)

# ── 绘画模式颜色方案(源自 Typhoon4 / WikiProject 路径图) ──
# Wikipedia 《Module:Storm categories/categories》官方色阶(与 _ty4_legend.png 采样一致):
#   Depression #6EC1EA / Storm #4DFFFF / Cat1 #FFFFD9 / Cat2 #FFD98C /
#   Cat3 #FF9E59 / Cat4 #FF738A / Cat5 #A188FC (Ty4 实测用 #8D75E6)
WIKI_SCALE = {
    "DB": (150, 150, 150),
    "TD": (110, 193, 234),
    "TS": (77, 255, 255),
    "STS": (255, 255, 217),
    "C1": (255, 255, 217),
    "C2": (255, 217, 140),
    "C3": (255, 158, 89),
    "C4": (255, 115, 138),
    "C5": (141, 117, 230),
    "EX": (204, 204, 204),
    "WV": (0, 191, 255),
    "LO": (160, 160, 160),
    "MD": (0, 255, 0),
    "SS": (200, 140, 100),
    "SD": (130, 190, 255),
}
# 2023 新版色阶(与 _ty4_legend.png 图例采样一致 = Wikipedia 官方色阶; Ty4 实测 C5 用 #8D75E6)
COLOR_2023_SCALE = dict(WIKI_SCALE)
COLOR_2023_SCALE.update({
    "C2-": (255, 240, 170),
    "C3-": (255, 190, 115),
    "C4-ST": (200, 115, 190),
})
# 色阶方案注册: 'app' = 应用现有主色阶; 'wiki' = 经典 Wiki; '2023' = 2023 新版
COLOR_SCALE_SCHEMES = {
    "app": "应用现有色阶",
    "wiki": "经典 Wiki 色阶",
    "2023": "2023 新版色阶",
}
# 绘画图例 7 档(与 Ty4 英文图例顺序一致)
WIKI_LEGEND_ENTRIES = [
    ("Depression", "TD"),
    ("Storm", "TS"),
    ("Category 1", "C1"),
    ("Category 2", "C2"),
    ("Category 3", "C3"),
    ("Category 4", "C4"),
    ("Category 5", "C5"),
]
# 图例位置
LEGEND_POS_NONE = "none"
LEGEND_POS_TL = "tl"
LEGEND_POS_BL = "bl"
LEGEND_POS_TR = "tr"
LEGEND_POS_BR = "br"
LEGEND_POS_AUTO = "auto"
LEGEND_POS_NAMES = {
    LEGEND_POS_NONE: "不绘画",
    LEGEND_POS_TL: "左上",
    LEGEND_POS_BL: "左下",
    LEGEND_POS_TR: "右上",
    LEGEND_POS_BR: "右下",
    LEGEND_POS_AUTO: "自动",
}

BUTTON_BORDER = (70, 130, 180)
BUTTON_BG = (100, 150, 200)
BUTTON_DISABLED = (150, 150, 150)
BUTTON_HIGHLIGHT = (180, 220, 255)
BUTTON_HOVER_LIGHTEN = (120, 180, 230)
BUTTON_PRESS_DARKEN = (50, 100, 150)

ERROR_BG = (255, 100, 100)
ERROR_BORDER = (200, 0, 0)

INFO_BOX_BG = (255, 255, 255, 200)
INFO_BOX_BORDER = BUTTON_BORDER

SEASON_CLOCK_BG = (240, 248, 255)
SEASON_CLOCK_BORDER = BUTTON_BORDER
SEASON_CLOCK_QUARTER = (200, 200, 200)

CONTROL_PANEL_BG = (240, 248, 255)
CONTROL_PANEL_LINE = (180, 200, 220)
SPEED_BAR_BG = (200, 200, 210)
SPEED_BAR_FILL = BUTTON_BORDER

FUTURE_LINE_ALPHA = 128

FPS_GREEN = (0, 180, 0)
FPS_YELLOW = (220, 180, 0)
FPS_RED = (220, 30, 30)
ERROR_TIMEOUT_MS = 2000

OCEAN_AREA_LINE = (255, 255, 100, 60)

ACE_CHART_CURVE_COLOR = (255, 100, 100)
ACE_CHART_MAX_POINT_COLOR = (0, 200, 0)

# 暗色主题配色
SETTINGS_DARK_BG = (25, 28, 35, 235)
SETTINGS_DARK_OVERLAY = (0, 0, 0, 140)
SETTINGS_ACCENT = (121, 217, 255)
SETTINGS_ACCENT_DARK = (80, 165, 220)
SETTINGS_TEXT_LIGHT = (220, 220, 240)
SETTINGS_TEXT_DIM = (140, 145, 160)
SETTINGS_TAB_BG = (35, 38, 48)
SETTINGS_TAB_ACTIVE = (45, 50, 62)
SETTINGS_TAB_HOVER = (50, 55, 68)
SETTINGS_INPUT_BG = (40, 44, 55)
SETTINGS_INPUT_BORDER = (60, 65, 78)
SETTINGS_CHECKBOX_BG = (55, 60, 75)
SETTINGS_CHECKBOX_CHECK = SETTINGS_ACCENT
SETTINGS_TOGGLE_ON = (70, 130, 200)
SETTINGS_TOGGLE_OFF = (50, 55, 68)
SETTINGS_TAB_NAMES = ["通用", "显示", "地图", "播放", "ACE", "数据"]


def settings_accent(dark_mode: bool, scheme: int = 1):
    """返回当前配色方案的高亮色。
    scheme=1: 暗色模式用中蓝 (80,165,220)
    scheme=2: v1.4.0 风格，暗色/亮色统一用亮蓝 (121,217,255)
    """
    if not dark_mode or scheme == 2:
        return SETTINGS_ACCENT
    return SETTINGS_ACCENT_DARK
