@echo off
setlocal enabledelayedexpansion

:: ============================================
set "self=%~nx0"
set "wlist=%temp%\rwl_%random%.txt"
set "nlist=%temp%\rnl_%random%.txt"
set "olist=%temp%\rol_%random%.txt"

:: 初始工作列表
dir /b /a-d 2>nul | findstr /v /i /x "%self%" > "%wlist%"

:: ============================================
:MAIN
:: ============================================
echo.
echo ============================================
echo    文件批量重命名工具
echo    目录: %cd%
echo    排除: %self%
echo ============================================
for /f %%c in ('type "%wlist%" 2^>nul ^| find /c /v ""') do set "cnt=%%c"
echo 工作文件数: %cnt%
if %cnt% equ 0 (
    echo 无文件可处理。
    goto :CLEANUP
)

:: --- 模式 ---
:ASK_MODE
set "mode="
echo.
echo 模式:
echo   1. 插入 - 在指定位置插入字符串
echo   2. 替换 - 将区间字符替换为新字符串
echo   3. 移动 - 将区间字符向前/后移动
echo.
set /p mode="请选择 (1/2/3, 回车退出): "
if "%mode%"=="" goto :CLEANUP
if "%mode%" neq "1" if "%mode%" neq "2" if "%mode%" neq "3" goto :ASK_MODE

:: --- 位置 a ---
:ASK_A
set "a="
echo.
set /p a="起始位置 a (0=最前, 1=第1字符后...): "
if not defined a goto :ASK_A
echo %a%|findstr /x "[0-9]*" >nul
if errorlevel 1 goto :ASK_A

:: ============ 模式分发 ============
if "%mode%"=="1" goto :MODE_INSERT
if "%mode%"=="2" goto :MODE_REPLACE
if "%mode%"=="3" goto :MODE_MOVE

:: ============ 插入模式 ============
:MODE_INSERT
:ASK_STR_INSERT
set "insert="
echo.
set /p insert="要插入的字符串 (可直接回车表示空串): "
goto :CONFIRM

:: ============ 替换模式 ============
:MODE_REPLACE
:: --- 输入 b ---
:ASK_B_REPLACE
set "b="
echo.
set /p b="终止位置 b (必须 >= %a%): "
if not defined b goto :ASK_B_REPLACE
echo %b%|findstr /x "[0-9]*" >nul
if errorlevel 1 goto :ASK_B_REPLACE
if %b% lss %a% (
    echo b 必须 ^>= %a% ！
    goto :ASK_B_REPLACE
)

:: --- 预览第一个文件的被替换原串 ---
call :PREVIEW_CHUNK %a% %b%

:: --- 输入新字符串 ---
:ASK_STR_REPLACE
set "insert="
echo.
set /p insert="替换为 (可直接回车表示删除): "
goto :CONFIRM

:: ============ 移动模式 ============
:MODE_MOVE
:: --- 输入 b ---
:ASK_B_MOVE
set "b="
echo.
set /p b="终止位置 b (必须 >= %a%): "
if not defined b goto :ASK_B_MOVE
echo %b%|findstr /x "[0-9]*" >nul
if errorlevel 1 goto :ASK_B_MOVE
if %b% lss %a% (
    echo b 必须 ^>= %a% ！
    goto :ASK_B_MOVE
)

:: --- 预览被移动的原串 ---
call :PREVIEW_CHUNK %a% %b%

:: --- 输入偏移量 ---
:ASK_OFFSET
set "offset="
echo.
echo 偏移量: 正数=向左移动, 负数=向右移动, 0=不移动
set /p offset="请输入偏移量: "
if not defined offset goto :ASK_OFFSET
echo %offset%|findstr /r "^-*[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo 请输入整数（如 2, -3, 0）！
    goto :ASK_OFFSET
)

:: --- 用第一个文件预览移动结果 ---
call :PREVIEW_MOVE %a% %b% %offset%
goto :CONFIRM

:: ============ 确认 ============
:CONFIRM
echo.
if "%mode%"=="1" echo 模式: 插入 - 在第 %a% 个字符后插入 "[%insert%]"
if "%mode%"=="2" echo 模式: 替换 - 将第 %a%+1 到第 %b% 个字符替换为 "[%insert%]"
if "%mode%"=="3" echo 模式: 移动 - 将第 %a%+1 到第 %b% 个字符移动 %offset% 位(正=左,负=右)
echo 工作列表:
type "%wlist%" 2>nul
echo.
set "cfm="
set /p cfm="确认执行？(y/n): "
if /i not "%cfm%"=="y" (
    echo 已取消。
    goto :ASK_CONTINUE
)

:: ============ 执行重命名 ============
echo.
echo 开始处理...
type nul > "%nlist%"
type nul > "%olist%"

for /f "usebackq delims=" %%F in ("%wlist%") do (
    if /i not "%%F"=="%self%" (
        if exist "%%F" (
            set "fname=%%~nF"
            set "fext=%%~xF"
            call :STRLEN fname flen
            set "skip=0"
            set "chopped="

            :: ===== 插入模式 =====
            if "%mode%"=="1" (
                if %a% gtr !flen! (
                    echo 跳过: "%%F" - 位置 %a% 超过主名长度 !flen!
                    set "skip=1"
                ) else (
                    set "pfx=!fname:~0,%a%!"
                    set "sfx=!fname:~%a%!"
                    set "newname=!pfx!!insert!!sfx!!fext!"
                )
            )

            :: ===== 替换模式 =====
            if "%mode%"=="2" (
                if %a% gtr !flen! (
                    echo 跳过: "%%F" - 起始 %a% 超过长度 !flen!
                    set "skip=1"
                ) else if %b% gtr !flen! (
                    echo 跳过: "%%F" - 终止 %b% 超过长度 !flen!
                    set "skip=1"
                ) else (
                    set "pfx=!fname:~0,%a%!"
                    set "sfx=!fname:~%b%!"
                    set /a "sl2 = b - a"
                    for %%L in (!sl2!) do set "chopped=!fname:~%a%,%%L!"
                    set "newname=!pfx!!insert!!sfx!!fext!"
                )
            )

            :: ===== 移动模式 =====
            if "%mode%"=="3" (
                if %a% gtr !flen! (
                    echo 跳过: "%%F" - 起始 %a% 超过长度 !flen!
                    set "skip=1"
                ) else if %b% gtr !flen! (
                    echo 跳过: "%%F" - 终止 %b% 超过长度 !flen!
                    set "skip=1"
                ) else (
                    set /a "clen = b - a"
                    for %%L in (!clen!) do set "chopped=!fname:~%a%,%%L!"
                    :: 移除区间后的剩余部分
                    set "pfx=!fname:~0,%a%!"
                    set "sfx=!fname:~%b%!"
                    set "rem=!pfx!!sfx!"
                    call :STRLEN rem remLen
                    :: 计算新位置
                    set /a "newPos = a - offset"
                    if !newPos! lss 0 set "newPos=0"
                    if !newPos! gtr !remLen! set "newPos=!remLen!"
                    :: ★ 用 for 中转避免嵌套 !! —— 这是关键修复
                    for %%P in (!newPos!) do (
                        set "left=!rem:~0,%%P!"
                        set "right=!rem:~%%P!"
                    )
                    set "newname=!left!!chopped!!right!!fext!"
                )
            )

            if "!skip!"=="0" (
                if /i "!newname!"=="%%F" (
                    echo 跳过: "%%F" - 名称未变
                ) else if exist "!newname!" (
                    echo 跳过: "%%F" - "!newname!" 已存在
                ) else (
                    if "%mode%"=="3" (
                        echo 移动: "%%F" --^> "!newname!"  块:[!chopped!]
                    ) else if "%mode%"=="2" (
                        echo 替换: "%%F" --^> "!newname!"  原串:[!chopped!]
                    ) else (
                        echo 插入: "%%F" --^> "!newname!"
                    )
                    ren "%%F" "!newname!" 2>nul
                    if errorlevel 1 (
                        echo 失败: "%%F"
                    ) else (
                        echo !newname!>> "%nlist%"
                        echo %%F>> "%olist%"
                    )
                )
            )
        ) else (
            echo 跳过: "%%F" - 不存在
        )
    )
)

echo.
echo 处理完成！

:: ============ 是否继续 ============
:ASK_CONTINUE
echo.
set "cont="
set /p cont="再来一次？(y/n): "
if /i not "%cont%"=="y" (
    echo 脚本结束。
    goto :CLEANUP
)

:ASK_SCOPE
echo.
echo 操作范围:
echo   1. 刚刚重命名的文件
echo   2. 还未重命名的文件
echo   3. 全部文件
set "scp="
set /p scp="选择 (1/2/3): "
if "%scp%"=="1" (
    if exist "%nlist%" ( copy /y "%nlist%" "%wlist%" >nul ) else ( type nul > "%wlist%" )
    echo 工作列表 -- 刚重命名的文件。
) else if "%scp%"=="2" (
    if exist "%olist%" (
        set "tmpf=%temp%\rut_%random%.txt"
        type nul > "!tmpf!"
        for /f "usebackq delims=" %%X in ("%wlist%") do (
            findstr /x /c:"%%X" "%olist%" >nul 2>&1
            if errorlevel 1 echo %%X>> "!tmpf!"
        )
        move /y "!tmpf!" "%wlist%" >nul
    )
    echo 工作列表 -- 仍未重命名的文件。
) else if "%scp%"=="3" (
    dir /b /a-d 2>nul | findstr /v /i /x "%self%" > "%wlist%"
    echo 工作列表 -- 全部文件。
) else (
    goto :ASK_SCOPE
)
goto :MAIN

:: ============================================
::  预览区间字符（替换/移动共用）
:: ============================================
:PREVIEW_CHUNK
set "pv_chunk="
set "pv_done=0"
for /f "usebackq delims=" %%F in ("%wlist%") do (
    if "!pv_done!"=="0" (
        set "pv_done=1"
        if exist "%%F" (
            set "pv_name=%%~nF"
            call :STRLEN pv_name pv_len
            if %1 leq !pv_len! if %2 leq !pv_len! (
                set /a "pv_sl = %2 - %1"
                for %%L in (!pv_sl!) do set "pv_chunk=!pv_name:~%1,%%L!"
                echo.
                echo 示例文件: %%F
                echo 区间字符:   [!pv_chunk!]
            ) else (
                echo.
                echo 示例文件: %%F (主名长度 !pv_len!，位置可能超出)
            )
        )
    )
)
exit /b

:: ============================================
::  预览移动结果
:: ============================================
:PREVIEW_MOVE
set "pv_done=0"
set "pv_chunk="
for /f "usebackq delims=" %%F in ("%wlist%") do (
    if "!pv_done!"=="0" (
        set "pv_done=1"
        if exist "%%F" (
            set "pv_name=%%~nF"
            set "pv_ext=%%~xF"
            call :STRLEN pv_name pv_len
            if %1 leq !pv_len! if %2 leq !pv_len! (
                set /a "pv_clen = %2 - %1"
                for %%L in (!pv_clen!) do set "pv_chunk=!pv_name:~%1,%%L!"
                set "pv_pfx=!pv_name:~0,%1!"
                set "pv_sfx=!pv_name:~%2!"
                set "pv_rem=!pv_pfx!!pv_sfx!"
                call :STRLEN pv_rem pv_remLen
                set /a "pv_newPos = %1 - %3"
                if !pv_newPos! lss 0 set "pv_newPos=0"
                if !pv_newPos! gtr !pv_remLen! set "pv_newPos=!pv_remLen!"
                :: ★ 用 for 中转避免嵌套 !!
                for %%P in (!pv_newPos!) do (
                    set "pv_left=!pv_rem:~0,%%P!"
                    set "pv_right=!pv_rem:~%%P!"
                )
                set "pv_result=!pv_left!!pv_chunk!!pv_right!!pv_ext!"
                echo.
                echo 示例文件:   %%F
                echo 被移动的块: [!pv_chunk!]
                echo 移动后预览: !pv_result!
            ) else (
                echo.
                echo 示例文件: %%F (主名长度 !pv_len!，位置可能超出)
            )
        )
    )
)
exit /b

:: ============================================
::  字符串长度计算
:: ============================================
:STRLEN
set "s=!%~1!#"
set "len=0"
for %%P in (4096 2048 1024 512 256 128 64 32 16 8 4 2 1) do (
    if "!s:~%%P,1!" NEQ "" (
        set /a "len+=%%P"
        set "s=!s:~%%P!"
    )
)
set "%~2=%len%"
exit /b

:: ============================================
:CLEANUP
:: ============================================
del /f /q "%wlist%" 2>nul
del /f /q "%nlist%" 2>nul
del /f /q "%olist%" 2>nul
endlocal
pause
exit /b