#!/bin/bash
# probe_server2.sh — 探测 zhenjie105_5 容器 + 服务器项目现状
echo '=== zhenjie105_5 container ==='
docker exec zhenjie105_5 sh -c '
  echo "python3: $(python3 --version 2>&1)"
  python3 -c "import cv2; print(\"cv2\", cv2.__version__)" 2>&1 | head -1
  python3 -c "import numpy; print(\"numpy\", numpy.__version__)" 2>&1 | head -1
  python3 -c "import torch; print(\"torch\", torch.__version__, \"cuda\", torch.cuda.is_available())" 2>&1 | head -1
  python3 -c "import onnxruntime; print(\"ort\", onnxruntime.__version__)" 2>&1 | head -1
  echo "pwd: $(pwd)"
  ls /data 2>&1 | head -5
  echo "--- mounts ---"
'
echo
echo '=== server project /data/zhenjie/zj869 ==='
ls -la /data/zhenjie/zj869 2>&1 | head -30
echo
echo '=== git log ==='
cd /data/zhenjie/zj869 && git log --oneline -5 2>&1
echo
echo '=== data dirs ==='
ls /data/zhenjie/zj869/data 2>&1
ls /data/zhenjie/zj869/data/crawl 2>&1
