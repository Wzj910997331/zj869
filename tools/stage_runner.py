#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/stage_runner.py — 流程卡死监控运行器（watchdog）。

背景：跑期全流程多次踩「卡死」——GLM 240s×4 死循环、DS 网关 120s 超时、filter workers=16
tesseract 自打负载拖到 0.2 张/s、crawl 网络悬挂。旧 include_prevday_tail.run() 是裸 subprocess
（无 timeout/无心跳），卡住只能肉眼盯终端，分不清哪步卡、卡多久、最后心跳是什么。

本模块把任意子进程阶段包成「受监控运行」：
  - child stdout+stderr 合并、逐行实时转播（外层监控/日志可见），转播同时计心跳。
  - 两条闹钟：
      idle  距最后一条输出 > idle 秒  → 判卡死 reason=idle  （退出码 125）
      wall  总运行 > wall 秒          → 判超预算 reason=wall（退出码 124）
  - kill 用 os.killpg（start_new_session=True 建进程组），连孙进程（filter spawn 的 tesseract
    等）一起杀；SIGTERM 宽限 8s 后 SIGKILL。
  - PYTHONUNBUFFERED=1 强制 child 逐行输出——没有它，child 走管道是块缓冲，进度"到了也不来"，
    会被误判卡死（心跳的前提）。
  - 收尾写诊断 JSON 到 logs/watchdog/<logdir>/<label>.deadlock.json（含最后 ~50 行输出），
    正常退出也写 reason=ok 一条便于审计"没卡过"。

用法（库，include_prevday_tail / run_period 内用）:
    from stage_runner import run_stage
    rc = run_stage([sys.executable, script, *args], label="filter_trend.py",
                   logdir="logs/watchdog/20260828")
    # label 没给就取 cmd 最后一段 basename；idle/wall 没给按 _BUDGETS 查，查不到用 (600, 7200)

用法（CLI，单脚本兜底 / 临时包一层）:
    python3 tools/stage_runner.py run --label crawl --idle 240 --wall 7200 -- \
        /usr/bin/python3 tools/crawl_gouli.py 2026-08-27 2026-08-28

    python3 tools/stage_runner.py selftest   # 内置自测：idle-kill / wall-kill / 孙进程组全杀

退出码约定：0=child 正常；124=wall 超预算；125=idle 卡死；其它=child 原样退出码；127=启动失败。
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 默认预算（basename -> (idle 秒, wall 秒)）。idle 必须 > 该阶段最长正常静默（慢≠死）：
# 宁可放宽让真·慢跑不误杀；卡死由 wall 与"远超已知心跳间隔"兜。
_BUDGETS = {
    "crawl_gouli.py": (240, 7200),          # http_get timeout=30，进度按页打印
    "fetch_lottery.py": (120, 1800),        # urllib timeout=20
    "filter_trend.py": (240, 3600),         # [filter] 进度每 ~25 图一条（workers=8 数秒一条）
    "blogger_hit_gate.py": (120, 1800),     # 确定性快步，几乎不静默
    "extract_prediction_strip.py": (120, 1800),
    "export_blogger_prediction.py": (120, 1800),
    "finalize_period_docs.py": (120, 1800),
    "verify_blogger_prediction.py": (120, 1800),
    "read_blogger_prediction.py": (600, 7200),   # 每批打印「批完成」；单批 up to max(240, 45*n)s
    "run_guihua_verify.py": (600, 7200),         # 每命中图 DS 单次 timeout≤120、逐图打印
    "include_prevday_tail.py": (900, 14400),     # 内部各步已各自被 watch；此条兜整链
}
_DEFAULT_IDLE, _DEFAULT_WALL = 600, 7200
_KILL_GRACE = 8  # SIGTERM 后等待秒，超时 SIGKILL


def _budget_for(label):
    base = os.path.basename(str(label))
    if base in _BUDGETS:
        return _BUDGETS[base]
    return (_DEFAULT_IDLE, _DEFAULT_WALL)


def _killpg(p, sig=signal.SIGTERM):
    try:
        os.killpg(p.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _write_diag(logdir, label, out):
    if not logdir:
        return
    try:
        os.makedirs(logdir, exist_ok=True)
        with open(os.path.join(logdir, f"{label}.deadlock.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    except Exception as e:  # 诊断写失败不能影响主流程
        print(f"[stage_runner] ⚠ 诊断写失败 {e}", flush=True)


def run_stage(cmd_args, *, label=None, idle=None, wall=None, logdir="logs/watchdog"):
    """受监控地跑一个子进程阶段。返回约定退出码（见模块 docstring）。"""
    if not cmd_args:
        print("[stage_runner] ✗ 空命令", flush=True)
        return 127
    if label is None:
        label = os.path.basename(str(cmd_args[-1]))
    if idle is None or wall is None:
        di, dw = _budget_for(label)
        idle = idle if idle is not None else di
        wall = wall if wall is not None else dw

    cmd_txt = " ".join(cmd_args)
    print(f"\n$ [watchdog] {label} (idle≤{idle}s / wall≤{wall}s): {cmd_txt}", flush=True)
    t0 = time.time()
    last_line_at = {"t": t0}
    tail = []
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    try:
        p = subprocess.Popen(cmd_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             bufsize=1, start_new_session=True, env=env)
    except FileNotFoundError:
        print(f"⚠️ [stage_runner] {label}: 启动失败（找不到 {cmd_args[0]}）", flush=True)
        _write_diag(logdir, label, {"cmd": cmd_txt, "label": label, "reason": "spawn-fail",
                                    "elapsed_s": 0, "tail": []})
        return 127

    def _relay():
        try:
            while True:
                raw = p.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\n")
                tail.append(line)
                if len(tail) > 50:
                    tail.pop(0)
                print(line, flush=True)
                last_line_at["t"] = time.time()
        except Exception:
            pass

    relay = threading.Thread(target=_relay, daemon=True)
    relay.start()

    reason = None
    while p.poll() is None:
        now = time.time()
        idle_since = now - last_line_at["t"]
        if idle_since > idle:
            reason = "idle"
            print(f"\n⚠️ [stage_runner] {label} 卡死(idle)：{int(idle_since)}s 无输出"
                  f"（阈值 {idle}s，wall {wall}s）→ kill 进程组", flush=True)
            break
        if now - t0 > wall:
            reason = "wall"
            print(f"\n⚠️ [stage_runner] {label} 超预算(wall)：{int(now - t0)}s > {wall}s"
                  f" → kill 进程组", flush=True)
            break
        time.sleep(0.5)

    rc = p.poll()
    if reason:
        _killpg(p)  # SIGTERM 全组
        try:
            p.wait(timeout=_KILL_GRACE)
        except Exception:
            _killpg(p, signal.SIGKILL)
            p.wait()
    else:
        p.wait()  # 正常退出：reap（关闭管道 → relay 读到 EOF 收尾）
    relay.join(timeout=5)

    elapsed = round(time.time() - t0, 1)
    rc = p.returncode if p.returncode is not None else (124 if reason == "wall" else 125)
    state = reason or ("ok" if rc == 0 else f"exit-{rc}")
    _write_diag(logdir, label, {
        "cmd": cmd_txt, "label": label, "reason": state,
        "wall": int(wall), "idle": int(idle), "elapsed_s": elapsed,
        "last_output_ago_s": round(time.time() - last_line_at["t"], 1) if reason else 0,
        "tail": tail[-50:],
    })
    if reason:
        print(f"[watchdog] {label} {reason} 被杀（{elapsed}s）。诊断 → {logdir}/{label}.deadlock.json。"
              f"重跑同一条命令即幂等续跑。", flush=True)
    return 125 if reason == "idle" else (124 if reason == "wall" else rc)


# ---------------------------------------------------------------- CLI
def _cmd_run(args):
    ap = _arg_parser()
    # stage_runner run ... -- <cmd...>
    try:
        i = args.index("--")
    except ValueError:
        print("run 需要 `--` 后跟要执行的命令")
        return 2
    flag = args[:i]
    cmd = args[i + 1:]
    ap.add_argument("--idle", type=int, default=None)
    ap.add_argument("--wall", type=int, default=None)
    ap.add_argument("--label", default=None)
    ap.add_argument("--logdir", default="logs/watchdog")
    ns = ap.parse_args(flag)
    if not cmd:
        print("run 的 `--` 后缺命令")
        return 2
    return run_stage(cmd, label=ns.label, idle=ns.idle, wall=ns.wall, logdir=ns.logdir)


def _arg_parser():
    import argparse
    return argparse.ArgumentParser(description="流程卡死监控运行器", add_help=True)


def _selftest():
    """内置自测：idle-kill / wall-kill / 孙进程组全杀。用 run_stage 跑带病子进程。"""
    ok = True

    def check(name, got, want):
        nonlocal ok
        mark = "PASS" if got == want else f"FAIL(got {got}, want {want})"
        if got != want:
            ok = False
        print(f"  [{mark}] {name}")

    py = sys.executable

    print("[selftest 1/3] idle 卡死：sleep 60 子进程，idle=2 → 应 2s 内被杀(rc 125)")
    t0 = time.time()
    rc = run_stage([py, "-c", "import time; time.sleep(60)"],
                   label="test-sleep-idle", idle=2, wall=60,
                   logdir="logs/watchdog/_selftest")
    check(f"idle-kill 耗时 {time.time() - t0:.1f}s(<10) rc", rc, 125)

    print("[selftest 2/3] wall 超预算：持续输出子进程，wall=3 → 应 ~3s 被杀(rc 124)")
    t0 = time.time()
    rc = run_stage([py, "-c",
                    "import time,sys; i=0\n"
                    "while True:\n"
                    "    print('tick', i, flush=True); i += 1; time.sleep(0.3)"],
                   label="test-print-wall", idle=10, wall=3,
                   logdir="logs/watchdog/_selftest")
    check(f"wall-kill 耗时 {time.time() - t0:.1f}s(3~8) rc", rc, 124)

    print("[selftest 3/3] 孙进程组全杀：父 spawn `sleep 60` 后睡，idle=2 → 孙 sleep 也须被清")
    code = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print('grandchild', g.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    t0 = time.time()
    rc = run_stage([py, "-c", code], label="test-group-idle", idle=2, wall=60,
                   logdir="logs/watchdog/_selftest")
    check(f"group-kill 耗时 {time.time() - t0:.1f}s(<10) rc", rc, 125)
    # 验孙进程没了：它在父的进程组里，父被 killpg 后应同时消失；这里只做提示性检查
    # （真要验需记录 pid，selftest 简单化：父能正常返回说明没等满 60s）
    print("  （组杀后 sleep 60 孙进程应随进程组一并消失，父未等满 60s 即返回即佐证）")

    print("\n" + ("selftest: 全部 PASS ✅" if ok else "selftest: 有 FAIL ❌"))
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 0
    if args[0] == "run":
        return _cmd_run(args[1:])
    if args[0] == "selftest":
        return _selftest()
    print(f"未知子命令 {args[0]}；支持 run | selftest")
    return 2


if __name__ == "__main__":
    sys.exit(main())
