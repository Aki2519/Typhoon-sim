# 台风路径模拟系统

热带气旋历史路径回放与统计分析工具。

## 运行

```
pip install -r requirements.txt
python main.py
```

## 操作

| 按键 | 功能 |
|------|------|
| `Space` | 播放 / 暂停 |
| `H` | 切换模式（正常 / 季节 / 编辑） |
| `[` `]` | 上一个 / 下一个台风 |
| `K` | 强度折线图 / ACE 图表 |
| `J` | 脚本对话框（选择 / 编辑 / 运行脚本） |
| `T` | 时间跳转（季节模式） |
| `O` | 台风列表 |
| `S` | 设置 |
| `G` | 点列表（编辑模式可编辑 / 正常模式只读） |
| `R` | 重置地图视图 |
| `P` | 截图 |
| `F1` | 隐藏 / 显示界面 |
| `←` `→` | 减速 / 加速 |
| `X` | 速度重置 |
| `F12` | 窗口置顶 |
| `I` | 编辑模式新建台风 |
| 右键拖拽 | 平移地图 |
| 滚轮 | 缩放地图 |

## 脚本格式示例

脚本引擎(`app/script_engine.py`)使用缩进来表示层级。首行的 `# 注释` 会作为脚本简介。

```
# 描述(脚本简介,首行)
jump 2026-04-12 00            # 顶层:时间跳到 2026-04-12 00
target 115 18 w=15             # 镜头对准 (115°E, 18°N),绘制区域宽度 15°
    arrive 2026-04-12 06       # 在 06 时到达该镜头
    *1.5                       # 到达后停留速度 1.5 倍
    depart 2026-04-15 00       # 00 时离开
repeat 2:                      # 下面缩进块循环 2 次
    target 120 20 w=20         # 镜头对准 (120°E, 20°N),宽度 20°
        arrive 2026-04-16 00
        *2                     # 停留速度 2 倍
    wait 5s                    # (真实时间)等待 5 秒,再进入下一次循环
```

语法说明(以 `script_engine.py` 解析器为准):

| 命令 | 含义 |
|------|------|
| `jump <datetime>` | 顶层时间跳转到指定时刻,如 `jump 2026-04-12 00` |
| `target <lon> <lat> w=<deg>` | 把镜头移到一个绘制目标:经度、纬度、绘制区域宽度(高度按屏幕绘制区域长宽比自动计算)。后面的 `arrive` / `depart` / `*速度` 子命令必须缩进在它下面 |
| `arrive <datetime>` | 镜头移动到目标的时间点。在 `arrive` 之前的 `*速度`(没有 `arrive` 时)是接近速度 |
| `depart <datetime>` | 开始离开该目标的时间点。在 `arrive` 与 `depart` 之间的 `*速度` 是停留速度;`depart` 之后的 `*速度` 是离开速度 |
| `*<speed>` | 速度倍数(如 `*1.5`、`*2`)。出现在 `target` 块内不同位置表示不同阶段速度;在顶层单独写 `*<speed>` 或 `speed <n>` 则为全局速度变更 |
| `jump <datetime>`(target 块内) | `depart` 之后立即把时间跳到该时刻(用于日期提前回退,见下) |
| `repeat <N>:` | 把下面缩进块整体重复 N 次,块内 `target` 与其子命令可逐次展开 |
| `wait <N>s` / `wait <N>m` | 按真实时间等待 N 秒 / N 分钟(如 `wait 5s`) |
| `wait` / `pause` | 等待用户交互(暂停),脚本在此停住 |

> 日期格式:`YYYY-MM-DD HH` 或 `YYYY-MM-DD HH:MM`(分钟按 30 分取整)。小时可省略。示例中 `arrive 2026-04-12 06` 即 2026-04-12 06 时。

行为说明:

- 运行脚本的按钮放在功能栏右边,名为"脚本";弹出界面列出所有脚本,按文件名排序,用小字展示简介。
- 用日期移动时,镜头移动速度取决于前后目标的时间差及游戏(模拟)速度:时间差越小、加速后镜头移动也越快;时间差越大则越慢。
- 脚本运行过程中不能用鼠标拖动,但可以暂停;暂停时脚本也暂停计时。
- 要把日期**提前回退**(后一个日期早于前一个日期):在 `target` 块内的 `depart` 之后写一行 `jump <更早的日期>`,镜头仍正常移动过去,移到的一瞬间时间跳跃到该日期(相当于旧格式中的符号 `=`)。

## 模拟器(架空台风, `simulator/`)

```
python simulator/run.py --seed 1 --years 3            # 界面模式
python simulator/run.py --seed 1 --years 3 --headless # 批处理
```

两条模拟内核(底栏下拉切换):

- **内核B 合成(默认)**: 气候模态(ENSO/SOI/PDO/AMO/IOD)驱动的合成场库 + analog 相似法生成逐月环境场,再逐 6h 物理状态机(引导气流/β漂移/PI 强度/眼壁置换)模拟台风。
- **内核A 真实大气**: 两级数据——
  1. **真 WRF 精算**(优先): `simulator/wrf/run_wrf.ps1` 一键管线(WSL2+Ubuntu 内编译 WRF/WPS,ERA5 初始/边界场经 CDS 下载,geogrid→ungrib→metgrid→real→wrf,wrfout 解析为 121×360 逐日场 `simulator/env/wrfdir/`),详见 `simulator/wrf/README.md`。
  2. **数据驱动**(无 WRF 场时自动回落): Open-Meteo 的 ECMWF IFS 9km(2017 至今,含近 92 天与 16 天预报)+ ERA5 再分析(1940 至今),免注册、逐日缓存于 `simulator/env/realdir/`。

```
python simulator/run.py --headless --kernel A --mode C --lat 18 --lon 128 --time "2026-08-01 00"
```

## 致谢

bilibili@麦块姚是屑

## 许可证

[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/deed.zh)
