# 极简打铃软件

## 特点

- 使用文本文件进行配置
- 不依赖于任何 Python 第三方库
- 兼容 Windows 与类 UNIX 操作系统

## 行为

- 每天定时加载打铃任务
- 到点自动播放指定音频文件
- 铃声冲突时立即切换到当前时刻的预定铃声

## 配置 / 运行

1. 安装 Python3（推荐3.12+）、Git: `<pkg_mgr> install python3 git`
2. 克隆源代码: `git clone https://github.com/Trichiurus-lepturus/simple-bell-ringer.git`
3. 仔细阅读`config.py.example`的每一项配置说明，按实际情况修改后，另存为`config.py`
4. 确保所有引用的`.csv`文件及音频文件存在、路径正确，`.csv`格式正确
5. 运行主程序: `<py_cmd> main.py`

---

*谨以此软件献给我敬爱的父亲*
