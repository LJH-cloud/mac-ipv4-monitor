# mac-ipv4-monitor (Python + PyObjC)

一个原生 macOS 透明悬浮层，用来实时显示：
- VPN 路径公网 IPv4
- 非 VPN（直连物理网卡）公网 IPv4

## 现在这版的 UI

- 无标题栏、纯透明背景浮层（无侧边按钮窗口）
- 单行小字体，类似歌词提示，不占地方
- 双击浮层：锁定/解锁拖动
- 右键浮层：锁定/解锁、穿透模式、立即刷新、重置位置、退出
- 穿透模式：浮层不拦截鼠标；在浮层区域右键一次可退出穿透
- 颜色区分状态：
  - 绿色：`VPN` 与 `D` 不同（直连探测正常）
  - 橙色：`VPN` 与 `D` 相同
  - 红色：任一路检测失败（`--`）
- 始终置顶，3 秒自动刷新

## 数据逻辑

- 多端点公网 IPv4 查询（自动回退）
- VPN 状态识别：`utun/ppp/ipsec/wg/tun/tap`
- 直连优先绑定物理网卡 `en* / bridge*`
- VPN 开启且直连失败时，使用上一次缓存的直连 IPv4 兜底
- 本地状态持久化：
  - `~/.mac_ipv4_monitor/state.json`

## venv 安装和运行

```bash
cd /path/to/mac-ipv4-monitor
./scripts/setup_venv.sh
./scripts/run.sh
```

前台运行时可用 `Ctrl+C` 直接终止。

## 自启动服务（launchd）

安装并立即启动（登录后自动拉起）：

```bash
cd /path/to/mac-ipv4-monitor
./scripts/install_service.sh
```

查看状态：

```bash
./scripts/service_status.sh
```

卸载服务：

```bash
./scripts/uninstall_service.sh
```

窗口拖拽位置和锁定状态会持久化到 `~/.mac_ipv4_monitor/state.json`，服务重启后自动恢复。

## 手动命令（可选）

```bash
cd /path/to/mac-ipv4-monitor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python python_ipv4_monitor.py
```

## 文件

- `python_ipv4_monitor.py`: 主程序（网络探测 + 原生 UI）
- `requirements.txt`: 依赖（PyObjC）
- `scripts/setup_venv.sh`: 初始化 venv
- `scripts/run.sh`: 前台启动（支持 `Ctrl+C` 停止）
- `scripts/install_service.sh`: 安装登录自启动服务
- `scripts/uninstall_service.sh`: 卸载服务
- `scripts/service_status.sh`: 查看服务状态
