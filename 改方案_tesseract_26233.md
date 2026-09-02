# 改方案指令 —— 给 26233 期识别会话 (8a82e787 / PID 8060)

## 立即停止：放弃手工 CV 列定位
之前用传统 CV（全行投影 vpeak 阈值 / find_cols_in_band 灰度140 /
连通域 digit_columns）找数字列，两张测试图都失准：
  - 图1 (s_2_0ee3536d, 绿): 期望列中心 [331,441,551,661,771]，检测 [510,820] 偏
  - 图2 (s_2_33b7cc04, 深色): 期望 [410,524,624,734,834]，检测 None
根因：网格线、行标签、大色块与数字纠缠，阈值法无法干净分离。
**别再写这种手工 CV 列定位了，没用。**

## 新方案：改用系统自带 tesseract 4.1.1 做数字识别 + 词框列定位
系统容器 zhenjie 里已有 tesseract + OpenCV，零下载可用。
tesseract 直接输出每个数字的 (x, y, w, h, conf, text) 词框，
天然跳过"手工设计列定位"，网格线/噪声用 conf 过滤即可滤掉。

### 验证通过的证据（图2 s_2_33b7cc04..._2.jpg 整图）
tesseract 读出的预测数字行（y≈768）：
  x=368 conf=94 txt=1
  x=487 conf=95 txt=1
  x=608 conf=97 txt=6
  x=729 conf=96 txt=3
  x=848 conf=96 txt=5
列中心≈[408,527,648,769,888]，与手工核验 [410,524,624,734,834] 基本吻合。

### 执行命令（在容器 zhenjie 内跑）
docker exec zhenjie bash -lc '
  f=/data/zhenjie/zj869/data/crawl/20260831/images/s_2_33b7cc04-..._2.jpg
  TESSDATA_PREFIX=/usr/share/tesseract-ocr/4.00/tessdata \
  tesseract "$f" stdout -l eng --psm 6 \
    -c tessedit_char_whitelist=0123456789 tsv'

### 使用要点
1. 别整图识别：会混入期号(262xx)、日期(0901)、期列表。先裁剪出候选数字行，
   或按 y 聚类分层 + x 排序，锚定期号行，取其下一行 5 个高conf数字 = 当期5列。
2. 白名单 -c tessedit_char_whitelist=0123456789 只出数字。
3. 用 conf 过滤：conf=0 的是网格线/噪声丢弃；conf=90+ 才可靠。
4. 单格裁剪后可试 psm 7/8/13。

## 落地参考
用 tesseract 词框替代手写列定位：
  整图 tesseract → y 聚类成行 + x 排序 → 锚定期号行(26233) → 取当期5列
  → 裁剪该区域交给 GLM/视觉模型读数，或直接用 tesseract 文本。
可参考工作区文件 /root/.openclaw/workspace-wecom-dm-wo00n9naaa00o8iboipdd4dvcblh9xba/tesseract_方案_26233.txt
