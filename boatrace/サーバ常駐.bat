@echo off
cd /d "%~dp0"
if not exist data mkdir data
rem 更新サーバを常駐起動（このプロセスが動き続ける＝タスクは実行中のまま）
py -3.13 serve_odds.py --port 8787 >> data\serve.log 2>&1
