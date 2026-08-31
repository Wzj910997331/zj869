#!/bin/bash
# probe_server.sh — 探测 10.5.64.5 服务器与 zhenjie 容器环境
echo '=== docker ps ==='
docker ps --format '{{.Names}} | {{.Image}} | {{.Status}}'
echo
echo '=== host python ==='
python3 --version 2>&1 || true
echo
echo '=== zhenjie container ==='
docker exec zhenjie sh -c '
  echo "pwd: $(pwd)"
  echo "python3: $(python3 --version 2>&1)"
  ls -d /data/zhenjie/zj869 2>/dev/null && echo "project dir exists"
  echo "--- venv? ---"
  ls /data/zhenjie/zj869/.venv/bin/python 2>/dev/null && /data/zhenjie/zj869/.venv/bin/python --version 2>&1 || echo "no venv"
  echo "--- key pkgs (venv) ---"
  /data/zhenjie/zj869/.venv/bin/pip list 2>/dev/null | grep -iE "opencv|numpy|torch|onnx|pillow|scikit|tensor" || echo "none found"
  echo "--- key pkgs (system) ---"
  python3 -c "import cv2; print(\"sys cv2\", cv2.__version__)" 2>&1 | head -1
  python3 -c "import numpy; print(\"sys numpy\", numpy.__version__)" 2>&1 | head -1
  echo "--- disk ---"
  df -h /data 2>/dev/null | tail -1
  echo "--- mem ---"
  free -m 2>/dev/null | head -2
'
