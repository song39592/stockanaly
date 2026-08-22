@echo off
chcp 65001 >nul
title 红色小牛 dsh 服务
cd /d "D:\ai\agent_dsh"

rem 已在 3080 运行则直接打开 webui，避免端口冲突
curl -s -m 2 http://127.0.0.1:3080/ >nul 2>&1
if not errorlevel 1 goto open

echo 启动红色小牛 dsh 服务（webui: http://127.0.0.1:3080）...
node_modules\.bin\dsh.cmd web --patch cordis.patch.yml
pause
exit

:open
echo 红色小牛 dsh 服务已在运行，打开 webui...
start "" "http://127.0.0.1:3080"
pause
exit
