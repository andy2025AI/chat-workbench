#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
python3 server.py --open
result=$?
if [ "$result" -ne 0 ]; then
  read -r "?启动未完成，按回车关闭。"
fi
exit "$result"
