"""PyInstaller 打包入口。

之所以单独放一个脚本，是因为 campuslink/app.py 内部用的是相对导入，
直接把它当脚本跑会失败。
"""

from __future__ import annotations

import sys

from campuslink.app import main

if __name__ == "__main__":
    sys.exit(main())
