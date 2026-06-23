@echo off
cd /d "%~dp0"
title 競艇 当日予想 ランチャー
echo ============================================================
echo  競艇 当日予想 を開きます
echo  (サーバを自動起動して、正しいURLでブラウザを開きます)
echo ============================================================
echo.

rem --- Python(3.13)の呼び出し方を決定（py -3.13 → py → python の順） ---
set "PY="
py -3.13 --version >nul 2>&1 && set "PY=py -3.13"
if not defined PY ( py --version >nul 2>&1 && set "PY=py" )
if not defined PY ( python --version >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo × Python が見つかりません（py / python のどちらも不可）。
  echo   Python 3.13 をインストールしてください。
  goto error
)

rem --- すでに更新サーバが動いているか確認（8787に繋がるか） ---
curl -s -o nul --max-time 2 http://localhost:8787/today.html
if %errorlevel%==0 (
  echo サーバは既に起動しています。ブラウザを開きます...
  goto open
)

echo 更新サーバを起動します（%PY%）...
start "競艇 更新サーバ（閉じると更新不可）" /min %PY% serve_odds.py --port 8787

rem --- 起動待ち（最大約30秒） ---
set /a tries=0
:wait
curl -s -o nul --max-time 2 http://localhost:8787/today.html
if %errorlevel%==0 goto open
set /a tries+=1
if %tries% geq 30 (
  echo.
  echo × サーバの起動を確認できませんでした。
  echo   最小化された「競艇 更新サーバ」窓のエラーを確認してください。
  goto error
)
ping -n 2 127.0.0.1 >nul
goto wait

:open
echo.
echo ブラウザを開きます: http://localhost:8787/today.html
start "" "http://localhost:8787/today.html"
echo.
echo ○ 当日予想を開きました（http://localhost:8787/today.html）。
echo   「更新」ボタンで当日の展示・結果を取得できます。
echo   ※ 最小化された「競艇 更新サーバ」窓は開いたままにしてください。
echo.
echo   （この窓は数秒後に自動で閉じます）
timeout /t 4 >nul
exit /b 0

:error
echo.
echo ------------------------------------------------------------
echo  開けませんでした。上のエラー内容を確認してください。
echo  URLを手動で開く場合: http://localhost:8787/today.html
pause >nul
exit /b 1
