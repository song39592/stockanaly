@echo off
chcp 65001 >nul
title 红色小牛 dsh 服务
cd /d "D:\ai\agent_dsh"
echo 启动红色小牛 dsh 服务（webui: http://127.0.0.1:3080）...
node_modules\.bin\dsh.cmd web --patch cordis.patch.yml
pause
