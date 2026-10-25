"""
server.py —— 聊天室服务器
============================================================
架构：
    - 主线程：accept 新连接，每个连接交给一个 ClientHandler 线程
    - ClientHandler 线程：读取该客户端的 JSON 消息，分发处理
    - ChatServer：保存所有在线客户端（username -> ClientHandler）

通信协议：每行一个 JSON 对象，字段 type 表示消息类型。
    客户端 → 服务器：register / login / chat / private / kick / mute /
                     delete_account / export / import / clear_history /
                     get_user_list / ping
    服务器 → 客户端：login_ok / login_fail / register_ok / register_fail /
                     history / chat / private / offline_messages /
                     online_list / system / mute_state / kicked /
                     account_deleted / clear_ok / pong
============================================================
"""

import socket
import threading
import json
import os
import traceback
from datetime import datetime
from database import Database

# ---------------- 服务器配置 ----------------
HOST = "0.0.0.0"        # 监听所有网卡（局域网/穿透都能连）
PORT = 8888


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ClientHandler(threading.Thread):
    """一个客户端连接对应一个线程"""

    def __init__(self, server, conn, addr):
        super().__init__(daemon=True)
        self.server = server        # 反向引用 ChatServer
        self.conn = conn            # 客户端 socket
        self.addr = addr            # 客户端地址
        self.username = None        # 登录后的用户名
        self.is_admin = False       # 是否管理员
        self.send_lock = threading.Lock()   # 防止多线程同时写 socket 造成乱序

    # ==================== 发送 ====================
    def send(self, msg: dict):
        """把 dict 序列化为一行 JSON 发送给客户端"""
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self.send_lock:
            try:
                self.conn.sendall(data)
            except Exception:
                pass    # 连接已断开就忽略

    # ==================== 线程主循环 ====================
    def run(self):
        """不断读取客户端发来的一行行 JSON，交给 handle 处理"""
        try:
            f = self.conn.makefile("r", encoding="utf-8")
            while True:
                line = f.readline()
                if not line:
                    break               # 客户端断开
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    self.handle(msg)
                except Exception:
                    # 单个消息处理出错不影响连接，打印堆栈便于排查
                    traceback.print_exc()
        except Exception:
            pass
        finally:
            # 无论如何，断开时通知服务器移除自己
            self.server.remove_client(self)
            try:
                self.conn.close()
            except Exception:
                pass

    # ==================== 消息分发 ====================
    def handle(self, msg):
        """根据 type 字段分发到对应的处理方法"""
        t = msg.get("type")
        if t == "register":
            self.do_register(msg)
        elif t == "login":
            self.do_login(msg)
        elif t == "chat":
            self.do_chat(msg)
        elif t == "private":
            self.do_private(msg)
        elif t == "kick":
            self.do_kick(msg)
        elif t == "mute":
            self.do_mute(msg)
        elif t == "export":
            self.do_export(msg)
        elif t == "import":
            self.do_import(msg)
        elif t == "delete_account":
            self.do_delete_account(msg)
        elif t == "clear_history":
            self.do_clear_history()
        elif t == "get_user_list":
            self.send_user_list()
        elif t == "ping":
            self.send({"type": "pong"})

    # ==================== 注册 ====================
    def do_register(self, msg):
        username = str(msg.get("username", "")).strip()
        password = str(msg.get("password", ""))

        # 基本校验
        if len(username) < 2:
            self.send({"type": "register_fail", "reason": "用户名至少 2 个字符"})
            return
        if len(password) < 3:
            self.send({"type": "register_fail", "reason": "密码至少 3 个字符"})
            return

        ok, info = self.server.db.add_user(username, password)
        if ok:
            self.send({"type": "register_ok"})
            # 广播 + 刷新用户列表（新用户虽然没登录，也出现在列表里）
            self.server.broadcast({"type": "system",
                                   "content": f"新用户 {username} 注册成功",
                                   "time": now_str()})
            self.server.broadcast_user_list()
        else:
            self.send({"type": "register_fail", "reason": info})

    # ==================== 登录 ====================
    def do_login(self, msg):
        username = str(msg.get("username", "")).strip()
        password = str(msg.get("password", ""))

        if not username or not password:
            self.send({"type": "login_fail", "reason": "用户名和密码不能为空"})
            return

        ok, result = self.server.db.check_login(username, password)
        if not ok:
            self.send({"type": "login_fail", "reason": result})
            return

        # 检查是否已在线，并把自己注册到在线表
        with self.server.lock:
            if username in self.server.clients:
                self.send({"type": "login_fail", "reason": "该账号已在线"})
                return
            self.username = username
            self.is_admin = bool(result["is_admin"])
            self.server.clients[username] = self

        # 1) 告诉客户端登录成功
        self.send({"type": "login_ok", "username": username,
                   "is_admin": self.is_admin, "muted": bool(result["is_muted"])})

        # 2) 推送最近 50 条公共消息
        self.send({"type": "history", "messages": self.server.db.get_history(50)})

        # 3) 推送离线私聊消息（对方离线时留下的）
        offlines = self.server.db.get_undelivered_private(username)
        if offlines:
            self.send({"type": "offline_messages", "messages": offlines})
            self.server.db.mark_delivered([m["id"] for m in offlines])
            print(f"[离线消息] 向 {username} 推送了 {len(offlines)} 条")

        # 4) 通知其他人"我进来了"，并刷新所有人的用户列表
        self.server.broadcast({"type": "system",
                               "content": f"{username} 加入了聊天室",
                               "time": now_str()}, exclude=username)
        self.server.broadcast_user_list()

        print(f"[登录] {username} {self.addr}")

    def send_user_list(self):
        """把当前用户列表单独发给这个客户端（用于客户端主动拉取）"""
        all_users = self.server.db.get_all_users()
        with self.server.lock:
            online_names = set(self.server.clients.keys())

        result = []
        for u in all_users:
            name = u["username"]
            result.append({
                "username": name,
                "is_admin": bool(u["is_admin"]),
                "is_muted": bool(u["is_muted"]),
                "online": name in online_names,
            })
        self.send({"type": "online_list", "users": result})

    # ==================== 公共聊天 ====================
    def do_chat(self, msg):
        if not self.username:
            return
        content = str(msg.get("content", "")).strip()
        if not content:
            return

        # 被禁言的用户不能发言
        user = self.server.db.get_user(self.username)
        if user and user["is_muted"]:
            self.send({"type": "system", "content": "你已被禁言，无法发送消息",
                       "time": now_str()})
            return

        # 存库 + 广播
        self.server.db.save_message(self.username, content, "", "public")
        self.server.broadcast({
            "type": "chat",
            "sender": self.username,
            "content": content,
            "time": now_str(),
            "is_admin": self.is_admin,
        })

    # ==================== 私聊 ====================
    def do_private(self, msg):
        if not self.username:
            return
        target = str(msg.get("to", "")).strip()
        content = str(msg.get("content", "")).strip()
        if not target or not content:
            return
        if target == self.username:
            self.send({"type": "system", "content": "不能给自己发私聊"})
            return

        with self.server.lock:
            th = self.server.clients.get(target)

        ts = now_str()
        if th is not None:
            # --- 对方在线：实时推送，并标记已送达 ---
            self.server.db.save_message(self.username, content, target, "private", delivered=1)
            th.send({"type": "private", "sender": self.username, "to": target,
                     "content": content, "time": ts, "direction": "in"})
            self.send({"type": "private", "sender": self.username, "to": target,
                       "content": content, "time": ts, "direction": "out"})
        else:
            # --- 对方离线：存库，标记未送达，等对方上线后推送 ---
            self.server.db.save_message(self.username, content, target, "private", delivered=0)
            self.send({"type": "private", "sender": self.username, "to": target,
                       "content": content, "time": ts, "direction": "out"})
            self.send({"type": "system",
                       "content": f"{target} 当前离线，消息已保存，对方上线后会收到",
                       "time": ts})

    # ==================== 管理员：踢人 ====================
    def do_kick(self, msg):
        if not self.is_admin:
            self.send({"type": "system", "content": "只有管理员可以执行该操作"})
            return
        target = str(msg.get("target", "")).strip()
        with self.server.lock:
            th = self.server.clients.get(target)
        if th is None:
            self.send({"type": "system", "content": f"用户 {target} 不在线"})
            return

        # 通知对方被踢，然后强制关闭它的 socket
        th.send({"type": "kicked", "reason": f"你被管理员 {self.username} 移出聊天室"})
        try:
            th.conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            th.conn.close()
        except Exception:
            pass

    # ==================== 管理员：禁言 / 解禁 ====================
    def do_mute(self, msg):
        if not self.is_admin:
            self.send({"type": "system", "content": "只有管理员可以执行该操作"})
            return
        target = str(msg.get("target", "")).strip()
        muted = bool(msg.get("muted", True))

        self.server.db.set_muted(target, muted)
        self.server.broadcast({
            "type": "system",
            "content": f"{target} 已被{'禁言' if muted else '解除禁言'}",
            "time": now_str(),
        })

        # 如果对方在线，单独通知他状态变化
        with self.server.lock:
            th = self.server.clients.get(target)
        if th:
            th.send({"type": "mute_state", "muted": muted})

    # ==================== 注销账号 ====================
    def do_delete_account(self, msg):
        """
        权限规则：
            - 普通用户：只能删自己，且要验证密码
            - 管理员：可以删任意用户，无需验证密码
            - admin 账号受保护，任何人不可删
        """
        if not self.username:
            return

        target = str(msg.get("target", "")).strip()
        password = str(msg.get("password", ""))

        # 权限校验
        if target != self.username and not self.is_admin:
            self.send({"type": "system", "content": "你没有权限注销其他账号"})
            return

        # 删除自己时验证密码
        if target == self.username:
            ok, _ = self.server.db.check_login(self.username, password)
            if not ok:
                self.send({"type": "delete_fail", "reason": "密码错误，注销失败"})
                return

        # admin 保护
        if target == "admin":
            self.send({"type": "delete_fail", "reason": "管理员账号不可注销"})
            return

        ok, info = self.server.db.delete_user(target)
        if not ok:
            self.send({"type": "delete_fail", "reason": info})
            return

        print(f"[注销] {target}（操作人：{self.username}）")

        # 如果被删的用户正在线，通知并踢下线
        with self.server.lock:
            victim = self.server.clients.get(target)
        if victim:
            victim.send({"type": "account_deleted",
                         "reason": f"你的账号已被{'' if target == self.username else '管理员'}注销"})
            try:
                victim.conn.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                victim.conn.close()
            except Exception:
                pass

        # 通知所有人 + 刷新列表
        self.server.broadcast({"type": "system",
                               "content": f"账号 {target} 已被注销",
                               "time": now_str()})
        self.server.broadcast_user_list()

    # ==================== 管理员：导出数据 ====================
    def do_export(self, msg):
        if not self.is_admin:
            self.send({"type": "system", "content": "只有管理员可以导出数据"})
            return
        path = str(msg.get("path", "")).strip()
        if not path:
            return
        try:
            _, info = self.server.db.export_to_json(path)
            self.send({"type": "system", "content": info, "time": now_str()})
        except Exception as e:
            self.send({"type": "system", "content": f"导出失败：{e}", "time": now_str()})

    # ==================== 管理员：导入数据 ====================
    def do_import(self, msg):
        if not self.is_admin:
            self.send({"type": "system", "content": "只有管理员可以导入数据"})
            return
        path = str(msg.get("path", "")).strip()
        if not path or not os.path.exists(path):
            self.send({"type": "system", "content": "文件不存在"})
            return
        try:
            _, info = self.server.db.import_from_json(path)
            self.server.broadcast({"type": "system", "content": info, "time": now_str()})
            # 导完后重新推送一份历史
            self.send({"type": "history", "messages": self.server.db.get_history(50)})
        except Exception as e:
            self.send({"type": "system", "content": f"导入失败：{e}", "time": now_str()})

    # ==================== 管理员：清空聊天记录 ====================
    def do_clear_history(self):
        if not self.is_admin:
            self.send({"type": "system", "content": "只有管理员可以清空聊天记录"})
            return
        n = self.server.db.clear_messages()
        self.server.broadcast({"type": "clear_ok",
                               "content": f"聊天记录已被管理员清空（删除 {n} 条）"})


class ChatServer:
    """服务器主类：管理所有在线客户端"""

    def __init__(self, host=HOST, port=PORT):
        self.host = host
        self.port = port
        self.db = Database()
        self.clients = {}                   # username -> ClientHandler
        self.lock = threading.RLock()       # 保护 clients 字典

    # ==================== 广播工具 ====================
    def broadcast(self, msg, exclude=None):
        """给所有在线客户端发送同一条消息，可以排除某人"""
        with self.lock:
            handlers = list(self.clients.items())
        for name, h in handlers:
            if name == exclude:
                continue
            h.send(msg)

    def broadcast_user_list(self):
        """向所有客户端广播最新的"用户列表 + 在线状态" """
        all_users = self.db.get_all_users()
        with self.lock:
            online_names = set(self.clients.keys())

        result = []
        for u in all_users:
            name = u["username"]
            result.append({
                "username": name,
                "is_admin": bool(u["is_admin"]),
                "is_muted": bool(u["is_muted"]),
                "online": name in online_names,
            })
        self.broadcast({"type": "online_list", "users": result})

    # ==================== 客户端断线处理 ====================
    def remove_client(self, handler):
        """某个客户端断开时，从在线表移除并通知其他人"""
        name = None
        with self.lock:
            # 只有当前 handler 确实占据这个 username 时才删（避免误删重登的）
            if handler.username and self.clients.get(handler.username) is handler:
                del self.clients[handler.username]
                name = handler.username

        if name:
            print(f"[离线] {name}")
            self.broadcast({"type": "system",
                            "content": f"{name} 离开了聊天室",
                            "time": now_str()})
            self.broadcast_user_list()

    # ==================== 启动 ====================
    def start(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)    # 允许快速重启
        s.bind((self.host, self.port))
        s.listen(50)                                                # 最多 50 个等待连接

        print("=" * 52)
        print(f"  聊天室服务器已启动：{self.host}:{self.port}")
        print("  按 Ctrl+C 停止服务器")
        print("=" * 52)

        try:
            while True:
                conn, addr = s.accept()
                print(f"[连接] {addr}")
                # 每个连接启动一个独立线程
                ClientHandler(self, conn, addr).start()
        except KeyboardInterrupt:
            print("\n服务器已关闭")
        finally:
            s.close()


# ==================== 启动入口 ====================
if __name__ == "__main__":
    ChatServer().start()