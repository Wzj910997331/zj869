#!/bin/bash
# 全流程编排：26231 + 26232 两期 识别→总结→多位置复核→导出规律
# 用法: bash pipeline_26231_26232.sh 2>&1 | tee pipeline_26231_26232.log
set -u
cd /data/zhenjie/zj869
PY=python3
LOG_DIR=data/crawl

stage() { echo ""; echo "════════════════════════════════════════════"; echo "▶ [$1] $(date +%H:%M:%S) $2"; echo "════════════════════════════════════════════"; }

run_period() {
  local BASE=$1 PERIOD=$2 DRAW=$3 CALIB=$4 CALIB_DRAW=$5
  local OUT=data/crawl/$BASE/vision_patterns_full.json

  stage "识别" "$PERIOD 期 ($BASE) 串行识别，每张60s超时×3次跳过"
  $PY -u tools/recognize_patterns.py --base data/crawl/$BASE --period $PERIOD \
      --calib $CALIB --calib-draw "$CALIB_DRAW" --workers 1 \
      --out $OUT --resume || { echo "✗ 识别 $PERIOD 异常"; return 1; }
  echo "✓ 识别完成: $OUT"

  stage "总结" "$PERIOD 期 summarize → image_patterns_with_blogger.json"
  $PY -u tools/summarize_image_patterns.py --base $BASE --period $PERIOD \
      --calib $CALIB --draw "$DRAW" || { echo "✗ summarize $PERIOD 异常"; return 1; }

  stage "多位置复核" "$PERIOD 期 recheck_multipos（重读命中图）"
  $PY -u tools/recheck_multipos.py --base $BASE --period $PERIOD \
      --draw "$DRAW" --calib $CALIB --calib-draw "$CALIB_DRAW" --workers 1 \
      || { echo "✗ recheck $PERIOD 异常"; return 1; }

  stage "复核合并" "$PERIOD 期 apply_multipos_recheck"
  $PY -u tools/apply_multipos_recheck.py --base $BASE \
      || { echo "✗ apply $PERIOD 异常"; return 1; }

  stage "导出规律" "$PERIOD 期 export_rules → docs/规律/$PERIOD"
  $PY -u tools/export_rules.py --base $BASE --period $PERIOD \
      --draw "$DRAW" --calib $CALIB --calib-draw "$CALIB_DRAW" \
      || { echo "✗ export $PERIOD 异常"; return 1; }
}

echo "═══ 全流程编排启动 $(date '+%Y-%m-%d %H:%M:%S') ═══"

# 期1: 26231
run_period 20260829 26231 "1 8 7 9 9" 26230 "9 4 6 8 3"

# 期2: 26232
run_period 20260830 26232 "8 0 2 3 3" 26231 "1 8 7 9 9"

echo ""
echo "═══ 全部完成 $(date '+%H:%M:%S') ═══"
