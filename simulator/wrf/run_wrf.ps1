# simulator/wrf/run_wrf.ps1 —— 真 WRF 全流程启动器(Windows 侧)
# 流程: 检查 WSL → 一次性编译(WRF/WPS) → 下载 ERA5 初始/边界场(CDS, 优先)
#       → WSL 内跑 geogrid/ungrib/metgrid/real/wrf → wrfout 解析为 121×360 场 → 内核A 可用
# 用法:
#   .\run_wrf.ps1 -Start 2026-08-01 -Days 10            # 默认西太区域
#   .\run_wrf.ps1 -Start 2026-08-01 -Days 10 -Setup      # 含一次性编译
#   .\run_wrf.ps1 -Start 2026-08-01 -Days 10 -Lon0 100 -Lon1 200 -Lat0 0 -Lat1 45 -Dx 30000
param(
    [string]$Start = (Get-Date -Format 'yyyy-MM-dd'),
    [int]$Days = 10,
    [double]$Lon0 = 95.0, [double]$Lon1 = 205.0,
    [double]$Lat0 = 0.0, [double]$Lat1 = 50.0,
    [int]$Dx = 30000,
    [switch]$Setup,
    [switch]$SkipFetch,
    [switch]$SkipRun,
    [switch]$NoBogus,
    [double]$BogusVmax = 30,
    [double]$BogusLat = 0,
    [double]$BogusLon = 0,
    [double]$BogusRmw = 30,
    [int]$HistInterval = 12
)
$ErrorActionPreference = 'Stop'
$proj = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # 项目根
$wrfd = Join-Path $proj 'simulator\wrf'
$env:PYTHONUTF8 = '1'

function Get-WslPath([string]$win) {
    # G:\a\b → /mnt/g/a/b(WSL 挂载盘符必须小写)
    $u = $win -replace '\\', '/'
    if ($u -match '^([A-Za-z]):(.*)$') {
        return '/mnt/' + $matches[1].ToLower() + $matches[2]
    }
    return $u
}

Write-Host "== WRF 管线 =="
Write-Host "  区域: $($Lon0)E-$($Lon1)E $($Lat0)N-$($Lat1)N  dx=$($Dx)m  时段: $Start 起 $Days 天"
if ($HistInterval -lt 1 -or $HistInterval -gt 60) {
    Write-Host "输出步长超出范围(1-60 分钟), 已钳制为 $([math]::Max(1, [math]::Min(60, $HistInterval))) 分钟"
    $HistInterval = [math]::Max(1, [math]::Min(60, $HistInterval))
}
Write-Host "  输出步长: ${HistInterval} 分钟/帧"

# ── 1. WSL 检查 ──
$distro = ((wsl -l -q 2>$null | Out-String) -replace "`0", '').Trim()
if ($distro -notmatch 'Ubuntu') {
    Write-Host "未找到 WSL Ubuntu 发行版。请以管理员运行:" -ForegroundColor Yellow
    Write-Host "    wsl --install -d Ubuntu" -ForegroundColor Cyan
    Write-Host "完成后重启终端, 再运行本脚本。" -ForegroundColor Yellow
    Write-Host "当前 wsl -l -q 输出: [$distro]" -ForegroundColor Yellow
    exit 1
}
Write-Host "[1/6] WSL: $distro"

# ── 2. 一次性编译(WRF/WPS)──
if ($Setup -or -not (wsl -d Ubuntu -e bash -lc "test -f ~/wrf/WRF/main/wrf.exe && echo OK" 2>$null | Select-String 'OK')) {
    Write-Host "[2/6] 编译 WRF/WPS(首次约 30-60 分钟)…" -ForegroundColor Cyan
    $wslPath = Get-WslPath (Resolve-Path (Join-Path $wrfd 'setup_wsl.sh')).Path
    wsl -d Ubuntu -e bash -lc "bash '$wslPath'"
    if ($LASTEXITCODE -ne 0) { Write-Host "编译失败"; exit 1 }
} else {
    Write-Host "[2/6] WRF 已编译, 跳过"
}

# ── 3. 生成 namelist(由模板填时段/区域)──
Write-Host "[3/6] 生成 namelist…"
$s = [datetime]::ParseExact($Start, 'yyyy-MM-dd', $null)
$e = $s.AddDays($Days)
$runHours = $Days * 24
$refLat = ($Lat0 + $Lat1) / 2.0
$refLon = ($Lon0 + $Lon1) / 2.0
$lonSpan = $Lon1 - $Lon0
$latSpan = $Lat1 - $Lat0
$eWe = [int]([math]::Ceiling($lonSpan * 111000.0 / $Dx)) + 1
$eSn = [int]([math]::Ceiling($latSpan * 111000.0 / $Dx)) + 1
$geoPath = '$HOME/wrf/WPS_GEOG'

$wps = Get-Content (Join-Path $wrfd 'namelist.wps.template') -Raw -Encoding UTF8
$wps = $wps.Replace('__START__', $s.ToString('yyyy-MM-dd_HH:mm:ss')).Replace('__END__', $e.ToString('yyyy-MM-dd_HH:mm:ss'))
$wps = $wps.Replace('__E_WE__', $eWe).Replace('__E_SN__', $eSn).Replace('__DX__', $Dx)
$wps = $wps.Replace('__REF_LAT__', '{0:F1}' -f $refLat).Replace('__REF_LON__', '{0:F1}' -f $refLon)
$wps = $wps.Replace('__GEOG__', $geoPath)

$nml = Get-Content (Join-Path $wrfd 'namelist.input.template') -Raw -Encoding UTF8
foreach ($p in @{SY=$s.Year; SM=$s.Month; SD=$s.Day; SH=$s.Hour;
                 EY=$e.Year; EM=$e.Month; ED=$e.Day; EH=$e.Hour;
                 RUN_HOURS=$runHours; E_WE=$eWe; E_SN=$eSn; DX=$Dx;
                 HIST_INTERVAL=$HistInterval}.GetEnumerator()) {
    $nml = $nml.Replace("__$($p.Key)__", [string]$p.Value)
}

$caseWin = Join-Path $wrfd 'case'
New-Item -ItemType Directory -Force -Path $caseWin | Out-Null
Set-Content -Path (Join-Path $caseWin 'namelist.wps') -Value $wps -Encoding ASCII
Set-Content -Path (Join-Path $caseWin 'namelist.input') -Value $nml -Encoding ASCII

# 架空/未来台风: 配置涡旋注入(bogus)。默认 30kt 起, -NoBogus 跳过(真实历史台风)
if ($NoBogus) {
    Remove-Item (Join-Path $caseWin 'bogus.json') -ErrorAction SilentlyContinue
    Write-Host "已跳过涡旋注入(-NoBogus, 历史台风用 ERA5 自带环流)" -ForegroundColor Cyan
} elseif ($BogusVmax -gt 0) {
    if ($BogusLat -eq 0 -or $BogusLon -eq 0) {
        $BogusLat = [math]::Round(($Lat0 + $Lat1) / 2.0, 1)
        $BogusLon = [math]::Round(($Lon0 + $Lon1) / 2.0, 1)
    }
    $bogus = @{lat = $BogusLat; lon = $BogusLon; vmax = $BogusVmax; rmw = $BogusRmw}
    $bogus | ConvertTo-Json | Set-Content -Path (Join-Path $caseWin 'bogus.json') -Encoding ASCII
    Write-Host "涡旋注入: ($BogusLat,$BogusLon) ${BogusVmax}kt RMW=${BogusRmw}km" -ForegroundColor Cyan
}

# ── 4. 下载初始/边界场(ERA5 优先, GFS 备用)──
if (-not $SkipFetch) {
    $inp = Join-Path $caseWin 'input'
    New-Item -ItemType Directory -Force -Path $inp | Out-Null
    Write-Host "[4/6] 下载 ERA5 场(CDS, 6h 间隔, $Days 天)…" -ForegroundColor Cyan
    python (Join-Path $wrfd 'fetch_era5.py') --start $Start --days $Days `
        --lon0 $Lon0 --lon1 $Lon1 --lat0 $Lat0 --lat1 $Lat1 --out $inp
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERA5 下载失败, 改用 GFS(NOMADS)…" -ForegroundColor Yellow
        python (Join-Path $wrfd 'fetch_gfs.py') --start $Start --days $Days `
            --lon0 $Lon0 --lon1 $Lon1 --lat0 $Lat0 --lat1 $Lat1 --out $inp
    }
} else { Write-Host "[4/6] 跳过下载(--SkipFetch)" }

# ── 5. WSL 内跑 WRF ──
if (-not $SkipRun) {
    Write-Host "[5/6] WSL 内运行 WRF(geogrid→ungrib→metgrid→real→wrf, 数小时)…" -ForegroundColor Cyan
    $runWsl = Get-WslPath (Resolve-Path (Join-Path $wrfd 'run_wrf.sh')).Path
    $caseWsl = Get-WslPath $caseWin
    # 生成 WSL 侧搬运脚本(避免 PS 引号地狱)
    New-Item -ItemType Directory -Force -Path (Join-Path $caseWin 'tools') | Out-Null
    Copy-Item (Join-Path $wrfd 'bogus_vortex.py') (Join-Path $caseWin 'tools\') -Force
    Copy-Item (Join-Path $wrfd 'wrfout_to_fields.py') (Join-Path $caseWin 'tools\') -Force
    $bootstrap = @'
set -e
mkdir -p ~/wrf/case
cp -r CASE_WSL/* ~/wrf/case/ 2>/dev/null || true
mkdir -p ~/wrf/case/input
cp -r CASE_WSL/input/* ~/wrf/case/input/ 2>/dev/null || true
mkdir -p ~/wrf/case/tools
cp -r CASE_WSL/tools/* ~/wrf/case/tools/ 2>/dev/null || true
bash RUN_WSL
mkdir -p CASE_WSL/wrfout
cd ~/wrf/case/wrfout
for f in wrfout_*; do
  [ -f "$f" ] || continue
  cp "$f" "CASE_WSL/wrfout/$(echo "$f" | tr ':' '-')"
done
'@ -replace 'CASE_WSL', $caseWsl -replace 'RUN_WSL', $runWsl
    $bootWin = Join-Path $caseWin 'bootstrap.sh'
    Set-Content -Path $bootWin -Value $bootstrap -Encoding ASCII
    $bootWsl = Get-WslPath $bootWin
    wsl -d Ubuntu -e bash -lc "bash '$bootWsl'"
    if ($LASTEXITCODE -ne 0) { Write-Host "WRF 运行失败, 见 WSL 内日志"; exit 1 }
    Write-Host "wrfout → $caseWin\wrfout"
} else { Write-Host "[5/6] 跳过 WRF 运行(--SkipRun)" }

# ── 6. wrfout → 121×360 场 + 涡旋追踪(内核A 数据)──
Write-Host "[6/6] 解析 wrfout → wrfdir + 涡旋追踪…" -ForegroundColor Cyan
python (Join-Path $wrfd 'wrfout_to_fields.py') --wrfout (Join-Path $caseWin 'wrfout') `
    --period $Start --days $Days
$wrfdir = Join-Path $proj 'simulator\env\wrfdir'
$guessLat = [math]::Round(($Lat0 + $Lat1) / 2.0, 1)
$guessLon = [math]::Round(($Lon0 + $Lon1) / 2.0, 1)
python (Join-Path $wrfd 'track_vortex.py') --wrfout (Join-Path $caseWin 'wrfout') `
    --start $Start --days $Days --guess-lat $guessLat --guess-lon $guessLon `
    --out (Join-Path $wrfdir 'track.json')
if ($LASTEXITCODE -eq 0) {
    Write-Host "追踪完成 → $wrfdir\track.json(内核A 直接使用)" -ForegroundColor Green
}
Write-Host "完成! 模拟器内核A 将自动优先使用 WRF 场(见 simulator/env/wrfdir/)" -ForegroundColor Green
