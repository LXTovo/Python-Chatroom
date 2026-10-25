# Python 多用户在线聊天室系统 🐍

## 📖 项目简介
基于 Python 和 C/S 架构独立设计开发的桌面端多用户在线聊天室。支持多用户并发连接、实时通信及数据持久化，已通过 PyInstaller 打包为可独立运行的 `.exe` 桌面应用。

## 🛠️ 技术栈
*   **语言与架构**：Python, C/S 架构
*   **网络通信**：Socket (TCP), JSON 自定义应用层协议
*   **并发与多线程**：Threading, 线程锁 (Lock), 消息队列 (Queue)
*   **数据库与安全**：SQLite, SHA256 加盐哈希
*   **GUI 与打包**：Tkinter, PyInstaller

## ✨ 核心功能
*   **网络与并发**：基于原生 TCP Socket 搭建 C/S 模型；服务端采用多线程处理并发连接，利用线程锁保障共享数据及 Socket 读写的一致性。
*   **实时通信**：自定义 JSON 格式通信协议，支持公共群聊、一对一私聊，以及管理员管控（踢人、禁言）等实时指令。
*   **离线消息**：基于 SQLite 状态字段实现离线消息机制，目标用户不在线时消息自动入库，上线后由服务端主动推送并标记已读。
*   **GUI与安全**：使用 Tkinter 开发界面，通过“消息队列 + 主线程轮询”安全解决后台网络线程与主 UI 线程的数据同步；实现密码 SHA256 加盐哈希存储。

## 🚀 如何运行
1. 克隆本项目到本地：
   ```bash
   git clone https://github.com/LXTwo/Python-Chatroom.git
2. 运行服务端：python server.py
3. 运行客户端（可运行多个实例测试群聊）：python client.py
