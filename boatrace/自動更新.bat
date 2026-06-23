@echo off
cd /d "%~dp0"
if not exist data mkdir data
set LOG=data\auto_daily.log
echo ============================================================>> "%LOG%"
echo [%date% %time%] 自動生成 開始>> "%LOG%"
rem 当日予想を生成（取得・変換・特徴量・予測・ページ生成。ログに追記）
py -3.13 daily.py >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] daily.py 失敗（上のログを確認）>> "%LOG%"
  exit /b 1
)
rem 更新サーバが落ちていれば常駐タスクを起動（保険）
schtasks /run /tn "競艇 更新サーバ常駐" >nul 2>&1
echo [%date% %time%] 生成完了 http://localhost:8787/today.html>> "%LOG%"
exit /b 0
