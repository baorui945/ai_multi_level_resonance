# -*- coding: utf-8 -*-
r"""
warm_cache.py —— 一次性把所有「模式 × 扫描范围」的扫描结果算好并写入磁盘缓存。
之后启动 server.py 会直接读缓存，秒开。

用法：
    python webapp\warm_cache.py            # 全部范围
    python webapp\warm_cache.py hs300 mv200_500
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SYS_DIR = os.path.dirname(HERE)
sys.path.insert(0, SYS_DIR)
sys.path.insert(0, os.path.dirname(SYS_DIR))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:                                            # noqa: BLE001
    pass

import server as S                                          # noqa: E402

MODES = ('daily', 'weekly', 'res', 'cycle', 'start')


def main():
    scopes = sys.argv[1:] or list(S.SCOPE_LABEL.keys())
    t0 = time.time()
    S.boot()
    print('[warm] 数据 %s，范围 %d 个' % (S._ASOF.date(), len(scopes)), flush=True)
    for scope in scopes:
        for mode in MODES:
            t = time.time()
            res = S.scan(mode, scope)
            n = res['counts']
            print('  %-10s %-6s -> 上抛 %3d / 观察 %3d  (%.1fs)%s' % (
                scope, mode, n['up'], n['watch'], time.time() - t,
                ' [缓存]' if res.get('cached') else ''), flush=True)
    print('[warm] 完成，用时 %.1fs，缓存目录 %s' % (time.time() - t0, S.CACHE_DIR))


if __name__ == '__main__':
    main()
