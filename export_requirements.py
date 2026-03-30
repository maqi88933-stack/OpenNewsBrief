#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
导出项目依赖库到 requirements.txt 文件
"""

import subprocess
import sys

def export_requirements():
    """使用 pip freeze 导出依赖"""
    try:
        # 执行 pip freeze 命令
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True,
            text=True,
            check=True
        )
        
        # 写入到 requirements.txt 文件
        with open("requirements.txt", "w", encoding="utf-8") as f:
            f.write(result.stdout)
        
        print("✓ 依赖库已成功导出到 requirements.txt")
        print(f"\n共导出 {len(result.stdout.strip().split(chr(10)))} 个依赖包")
        
    except subprocess.CalledProcessError as e:
        print(f"✗ 执行 pip freeze 失败：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ 导出依赖失败：{e}")
        sys.exit(1)

if __name__ == "__main__":
    export_requirements()
