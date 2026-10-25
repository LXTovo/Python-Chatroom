"""
client.py —— 聊天室客户端（Tkinter 图形界面）
============================================================
架构：
    - Network 类：封装 socket，后台线程接收服务器消息，丢进 queue
    - App 类：图形界面 + 消息处理
    - 主线程每 80ms 从 queue 取消息（Tkinter 只能在主线程操作控件）
============================================================
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import socket
import threading
import json
import queue

# ---------------- 默认服务器地址 ----------------
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8888


# ==================== 网络层 ====================
class Network:
    """封装 socket 连接，后台线程接收消息，通过回调传给主线程"""

    def __init__(self, host, port, on_message):
        self.host = host
        self.port = port
        self.on_message = on_message      # 回调函数：把收到的消息放进 queue
        self.sock = None
        self.running = False

    def connect(self):
        """建立连接并启动接收线程"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(5)           # 连接超时 5 秒
        self.sock.connect((self.host, self.port))
        self.sock.settimeout(None)        # 连接后取消超时（否则接收会抛异常）
        self.running = True
        threading.Thread(target=self._recv_loop, daemon=True).start()

    def _recv_loop(self):
        """后台线程：不断读取一行行 JSON 消息"""
        try:
            f = self.sock.makefile("r", encoding="utf-8")
            while self.running:
                line = f.readline()
                if not line:
                    break                 # 服务器断开
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.on_message(msg)      # 丢进队列
        except Exception:
            pass
        finally:
            self.on_message({"type": "_disconnected"})

    def send(self, msg: dict):
        """发送一条消息，自动序列化为一行 JSON"""
        if not self.sock:
            return
        try:
            self.sock.sendall(
                (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
            )
        except Exception:
            self.on_message({"type": "_disconnected"})

    def close(self):
        """主动断开连接"""
        self.running = False
        try:
            if self.sock:
                self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None


# ==================== 主应用 ====================
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("多用户在线聊天室")
        self.root.geometry("940x640")
        self.root.minsize(760, 520)

        # --- 运行时状态 ---
        self.net = None                 # Network 实例
        self.msg_queue = queue.Queue()  # 网络线程 → 主线程的消息管道
        self.username = None            # 当前登录的用户名
        self.is_admin = False           # 是否管理员
        self.private_target = None      # 当前私聊对象（None 表示公共聊天）
        self.online_users = set()       # 在线用户集合（用于判断状态）
        self.closing = False            # 是否正在关闭程序

        self.build_login_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self.poll_queue)     # 定时轮询 queue
        self.root.mainloop()

    # ==================== 通用工具 ====================
    def _clear_root(self):
        """清空窗口所有控件，用于登录页 ↔ 主界面切换"""
        for w in self.root.winfo_children():
            w.destroy()
        self.root.config(menu=tk.Menu(self.root))   # 清空菜单栏

    def poll_queue(self):
        """
        主线程每 80ms 检查一次 queue。
        Tkinter 控件只能在主线程操作，所以不能在网络线程里直接改界面。
        """
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                self.handle_message(msg)
        except queue.Empty:
            pass
        self.root.after(80, self.poll_queue)

    # ==================== 登录界面 ====================
    def build_login_ui(self):
        """构建登录界面"""
        self._clear_root()
        f = ttk.Frame(self.root, padding=40)
        f.place(relx=0.5, rely=0.5, anchor="center")

        ttk.Label(f, text="多用户在线聊天室",
                  font=("微软雅黑", 20, "bold")).grid(row=0, column=0, columnspan=2, pady=(0, 26))

        ttk.Label(f, text="服务器地址：").grid(row=1, column=0, sticky="e", pady=6)
        self.host_var = tk.StringVar(value=SERVER_HOST)
        ttk.Entry(f, textvariable=self.host_var, width=22).grid(row=1, column=1, pady=6)

        ttk.Label(f, text="端口：").grid(row=2, column=0, sticky="e", pady=6)
        self.port_var = tk.StringVar(value=str(SERVER_PORT))
        ttk.Entry(f, textvariable=self.port_var, width=22).grid(row=2, column=1, pady=6)

        ttk.Label(f, text="用户名：").grid(row=3, column=0, sticky="e", pady=6)
        self.user_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.user_var, width=22).grid(row=3, column=1, pady=6)

        ttk.Label(f, text="密码：").grid(row=4, column=0, sticky="e", pady=6)
        self.pwd_var = tk.StringVar()
        pwd_entry = ttk.Entry(f, textvariable=self.pwd_var, show="*", width=22)
        pwd_entry.grid(row=4, column=1, pady=6)
        pwd_entry.bind("<Return>", lambda e: self.do_login())   # 回车快捷登录

        btns = ttk.Frame(f)
        btns.grid(row=5, column=0, columnspan=2, pady=22)
        ttk.Button(btns, text="登 录", width=10, command=self.do_login).pack(side="left", padx=8)
        ttk.Button(btns, text="注 册", width=10, command=self.do_register).pack(side="left", padx=8)

        ttk.Label(f, text="默认管理员账号：admin / admin123",
                  foreground="gray").grid(row=6, column=0, columnspan=2)

    def ensure_connected(self, host, port):
        """确保已连接服务器；未连接则新建连接"""
        if self.net is not None:
            return True
        try:
            self.net = Network(host, port, self.msg_queue.put)
            self.net.connect()
            return True
        except Exception as e:
            self.net = None
            messagebox.showerror("连接失败", f"无法连接服务器 {host}:{port}\n{e}")
            return False

    def _server_addr(self):
        """从界面读取服务器地址和端口"""
        host = self.host_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror("错误", "端口必须是数字")
            return None
        return host, port

    def do_login(self):
        """点击登录按钮"""
        addr = self._server_addr()                # 读地址端口
        if not addr:
            return
        username = self.user_var.get().strip()    # 读用户名
        password = self.pwd_var.get()             # 读密码
        if not username or not password:
            messagebox.showwarning("提示", "请输入用户名和密码")
            return
        if not self.ensure_connected(*addr):      # 确保连接
            return
        self.net.send({"type": "login", "username": username, "password": password})

    def do_register(self):
        """点击注册按钮"""
        addr = self._server_addr()
        if not addr:
            return
        username = self.user_var.get().strip()
        password = self.pwd_var.get()
        if not username or not password:
            messagebox.showwarning("提示", "请输入要注册的用户名和密码")
            return
        if not self.ensure_connected(*addr):
            return
        self.net.send({"type": "register", "username": username, "password": password})

    # ==================== 主界面 ====================
    def build_main_ui(self):
        """登录成功后构建主界面"""
        self._clear_root()
        self.private_target = None

        # --- 菜单栏 ---
        menubar = tk.Menu(self.root)

        sys_menu = tk.Menu(menubar, tearoff=0)
        sys_menu.add_command(label="导出数据到 JSON 文件", command=self.export_data)
        sys_menu.add_command(label="从 JSON 文件导入数据", command=self.import_data)
        sys_menu.add_separator()
        sys_menu.add_command(label="清空聊天记录", command=self.clear_history)
        sys_menu.add_separator()
        sys_menu.add_command(label="注销我的账号", command=self.delete_my_account)
        sys_menu.add_separator()
        sys_menu.add_command(label="退出登录", command=self.logout)
        sys_menu.add_command(label="退出程序", command=self.on_close)
        menubar.add_cascade(label="系统", menu=sys_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="关于", command=lambda: messagebox.showinfo(
            "关于", "多用户在线聊天室 v1.0\nPython + Socket + Tkinter + SQLite"))
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.config(menu=menubar)

        # --- 顶部：当前用户 + 在线计数 ---
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")
        self.title_label = ttk.Label(top, text="", font=("微软雅黑", 11, "bold"))
        self.title_label.pack(side="left")
        self.count_label = ttk.Label(top, text="在线：0 人", foreground="gray")
        self.count_label.pack(side="right")

        # --- 中部：左侧用户列表 + 右侧聊天区 ---
        mid = ttk.Frame(self.root)
        mid.pack(fill="both", expand=True, padx=6, pady=4)

        # 左侧用户列表
        left = ttk.LabelFrame(mid, text="在线用户（双击私聊 / 右键菜单）", width=190)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.user_listbox = tk.Listbox(left, exportselection=False, font=("微软雅黑", 10))
        self.user_listbox.pack(fill="both", expand=True, padx=4, pady=4)
        self.user_listbox.bind("<Double-Button-1>", lambda e: self.start_private_chat())
        self.user_listbox.bind("<Button-3>", self.popup_user_menu)

        # 右侧聊天记录区
        right = ttk.Frame(mid)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        text_wrap = ttk.Frame(right)
        text_wrap.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(text_wrap, command=lambda *a: self.chat_text.yview(*a))
        sb.pack(side="right", fill="y")
        self.chat_text = tk.Text(text_wrap, state="disabled", wrap="word",
                                 font=("微软雅黑", 10), yscrollcommand=sb.set)
        self.chat_text.pack(side="left", fill="both", expand=True)

        # 不同消息用不同颜色
        self.chat_text.tag_config("system", foreground="#888888")    # 系统消息：灰
        self.chat_text.tag_config("self", foreground="#0066cc")      # 自己：蓝
        self.chat_text.tag_config("other", foreground="#222222")     # 别人：黑
        self.chat_text.tag_config("private", foreground="#9900cc")   # 私聊：紫
        self.chat_text.tag_config("admin", foreground="#cc0000")     # 管理员：红

        # --- 私聊目标提示条 ---
        self.target_label = ttk.Label(self.root, text="发送到：公共聊天室",
                                      foreground="#0066cc")
        self.target_label.pack(fill="x", padx=10)
        self.target_label.bind("<Double-Button-1>", lambda e: self.clear_private_target())

        # --- 底部输入框 ---
        bottom = ttk.Frame(self.root, padding=6)
        bottom.pack(fill="x")
        self.input_var = tk.StringVar()
        self.input_entry = ttk.Entry(bottom, textvariable=self.input_var,
                                     font=("微软雅黑", 10))
        self.input_entry.pack(side="left", fill="x", expand=True)
        self.input_entry.bind("<Return>", lambda e: self.send_chat())
        ttk.Button(bottom, text="发送", width=10, command=self.send_chat)\
            .pack(side="left", padx=(8, 0))

        self.title_label.config(
            text=f"当前用户：{self.username}" + ("（管理员）" if self.is_admin else ""))
        self.input_entry.focus_set()

    def append_text(self, text, tag=None):
        """往聊天区追加一行文本（带颜色标签）"""
        self.chat_text.config(state="normal")
        self.chat_text.insert("end", text + "\n", tag)
        self.chat_text.see("end")
        self.chat_text.config(state="disabled")

    # ==================== 消息处理 ====================
    def handle_message(self, msg):
        """处理从服务器收到的一条消息"""
        t = msg.get("type")

        # ---- 登录结果 ----
        if t == "login_ok":
            self.username = msg["username"]
            self.is_admin = msg["is_admin"]
            self.build_main_ui()
            self.append_text(f"*** 欢迎回来，{self.username}！***", "system")
            # 主动请求一次用户列表（防止广播丢失）
            self.root.after(200, lambda: self.net.send({"type": "get_user_list"}))

        elif t == "login_fail":
            messagebox.showerror("登录失败", msg.get("reason", ""))

        # ---- 注册结果 ----
        elif t == "register_ok":
            messagebox.showinfo("提示", "注册成功，请点击登录")

        elif t == "register_fail":
            messagebox.showerror("注册失败", msg.get("reason", ""))

        # ---- 历史消息 ----
        elif t == "history":
            self.append_text("———— 最近聊天记录 ————", "system")
            for m in msg.get("messages", []):
                tag = "self" if m["sender"] == self.username else "other"
                self.append_text(f"[{m['created_at']}] {m['sender']}: {m['content']}", tag)
            self.append_text("——————————————", "system")

        # ---- 公共聊天消息 ----
        elif t == "chat":
            sender = msg["sender"]
            tag = "self" if sender == self.username else \
                ("admin" if msg.get("is_admin") else "other")
            prefix = "[管理员] " if msg.get("is_admin") else ""
            self.append_text(f"[{msg['time']}] {prefix}{sender}: {msg['content']}", tag)

        # ---- 私聊消息 ----
        elif t == "private":
            if msg.get("direction") == "in":
                self.append_text(
                    f"[{msg['time']}] {msg['sender']} 悄悄对你说：{msg['content']}", "private")
            else:
                self.append_text(
                    f"[{msg['time']}] 你悄悄对 {msg['to']} 说：{msg['content']}", "private")

        # ---- 离线私聊消息 ----
        elif t == "offline_messages":
            msgs = msg.get("messages", [])
            if msgs:
                self.append_text(f"*** 你有 {len(msgs)} 条离线消息 ***", "system")
                for m in msgs:
                    self.append_text(
                        f"[{m['created_at']}] {m['sender']} 悄悄对你说：{m['content']}",
                        "private"
                    )

        # ---- 注销结果 ----
        elif t == "delete_fail":
            messagebox.showerror("注销失败", msg.get("reason", ""))

        elif t == "account_deleted":
            messagebox.showwarning("账号被注销", msg.get("reason", "你的账号已被注销"))
            self.logout(silent=True)

        # ---- 系统通知 ----
        elif t == "system":
            self.append_text(f"[{msg.get('time', '')}] {msg['content']}", "system")

        # ---- 聊天记录被清空 ----
        elif t == "clear_ok":
            self.chat_text.config(state="normal")
            self.chat_text.delete("1.0", "end")
            self.chat_text.config(state="disabled")
            self.append_text(f"*** {msg['content']} ***", "system")

        # ---- 用户列表更新 ----
        elif t == "online_list":
            self.update_online_list(msg.get("users", []))

        # ---- 被禁言状态变化 ----
        elif t == "mute_state":
            state = "已被禁言" if msg["muted"] else "禁言已解除"
            messagebox.showinfo("提示", f"你{state}")

        # ---- 被踢出 ----
        elif t == "kicked":
            messagebox.showwarning("提示", msg.get("reason", "你已被移出聊天室"))
            self.logout(silent=True)

        # ---- 与服务器断开 ----
        elif t == "_disconnected":
            if self.username and not self.closing:
                messagebox.showwarning("连接断开", "与服务器的连接已断开")
                self.logout(silent=True)

    def update_online_list(self, users):
        """刷新左侧用户列表"""
        self.user_listbox.delete(0, "end")
        self.online_users = set()
        online_count = 0

        for u in users:
            name = u["username"]
            online = u.get("online", False)
            if online:
                self.online_users.add(name)
                online_count += 1

            # 拼装显示文本：状态圆点 + 名字 + 各种标记
            dot = "🟢" if online else "⚪"
            label = f"{dot} {name}"
            if u.get("is_admin"):
                label += "（管理员）"
            if name == self.username:
                label += "（我）"
            if not online:
                label += " 离线"
            if u.get("is_muted") and online:
                label += " 🔇"

            self.user_listbox.insert("end", label)

        self.count_label.config(text=f"在线 {online_count} 人 / 共 {len(users)} 人")

    # ==================== 发送消息 ====================
    def send_chat(self):
        """点击发送 / 回车时调用"""
        if not self.net or not self.username:
            return
        content = self.input_var.get().strip()
        if not content:
            return

        if self.private_target:
            self.net.send({"type": "private", "to": self.private_target, "content": content})
        else:
            self.net.send({"type": "chat", "content": content})
        self.input_var.set("")

    # ==================== 私聊相关 ====================
    def _selected_username(self):
        """从列表当前选中项中提取纯用户名（去掉各种前缀图标和标记）"""
        sel = self.user_listbox.curselection()
        if not sel:
            return None
        text = self.user_listbox.get(sel[0])
        text = text.replace("🟢", "").replace("⚪", "").replace("🔇", "").strip()
        return text.split("（")[0].split(" 离线")[0].strip()

    def start_private_chat(self):
        """双击用户列表时调用"""
        name = self._selected_username()
        if name:
            self.set_private_target(name)

    def set_private_target(self, name):
        """进入与某人的私聊模式"""
        if name == self.username:
            messagebox.showinfo("提示", "不能和自己私聊")
            return
        self.private_target = name
        if name in self.online_users:
            self.target_label.config(
                text=f"发送到：与 {name} 私聊（双击此行返回公共聊天）",
                foreground="#9900cc")
        else:
            self.target_label.config(
                text=f"发送到：与 {name} 私聊 · 对方当前离线，消息将在其上线后送达",
                foreground="#888888")

    def clear_private_target(self):
        """退出私聊模式，回到公共聊天"""
        self.private_target = None
        self.target_label.config(text="发送到：公共聊天室", foreground="#0066cc")

    # ==================== 右键菜单 ====================
    def popup_user_menu(self, event):
        """右键用户列表弹出菜单"""
        idx = self.user_listbox.nearest(event.y)
        if idx < 0:
            return
        self.user_listbox.selection_clear(0, "end")
        self.user_listbox.selection_set(idx)
        name = self._selected_username()
        is_online = name in self.online_users

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"与 {name} 私聊",
                         command=lambda: self.set_private_target(name))

        # 管理员专属菜单
        if self.is_admin and name != self.username:
            menu.add_separator()
            if is_online:
                menu.add_command(label="踢出聊天室", command=lambda: self.kick_user(name))
            menu.add_command(label="禁言", command=lambda: self.mute_user(name, True))
            menu.add_command(label="解除禁言", command=lambda: self.mute_user(name, False))
            menu.add_separator()
            menu.add_command(label="删除该账号",
                             command=lambda: self.delete_other_account(name),
                             foreground="red")

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def kick_user(self, name):
        if messagebox.askyesno("确认", f"确定把 {name} 踢出聊天室吗？"):
            self.net.send({"type": "kick", "target": name})

    def mute_user(self, name, muted):
        self.net.send({"type": "mute", "target": name, "muted": muted})

    # ==================== 数据导出 / 导入 ====================
    def export_data(self):
        """管理员：导出数据库到 JSON"""
        path = filedialog.asksaveasfilename(
            title="导出数据库内容",
            defaultextension=".json",
            initialfile="chatroom_export.json",
            filetypes=[("JSON 文件", "*.json")])
        if not path:
            return
        self.net.send({"type": "export", "path": path})

    def import_data(self):
        """管理员：从 JSON 导入数据"""
        path = filedialog.askopenfilename(
            title="选择要导入的 JSON 文件",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        if messagebox.askyesno("确认", "导入会把文件中的数据追加到数据库，是否继续？"):
            self.net.send({"type": "import", "path": path})

    def clear_history(self):
        """管理员：清空聊天记录"""
        if messagebox.askyesno("确认", "确定清空所有聊天记录吗？（不可恢复）"):
            self.net.send({"type": "clear_history"})

    # ==================== 注销账号 ====================
    def delete_my_account(self):
        """注销自己的账号（需要输入密码验证）"""
        if not messagebox.askyesno(
                "确认注销",
                "注销后账号和登录信息将永久删除，聊天记录保留。\n"
                "确定要注销吗？"):
            return
        pwd = simpledialog.askstring("身份验证", "请输入当前账号的密码：", show="*")
        if pwd is None:
            return
        if not pwd:
            messagebox.showwarning("提示", "密码不能为空")
            return
        self.net.send({"type": "delete_account",
                       "target": self.username, "password": pwd})

    def delete_other_account(self, name):
        """管理员：删除别人的账号"""
        if name == self.username:
            return
        if not messagebox.askyesno(
                "确认删除",
                f"确定删除账号 {name} 吗？\n该用户的登录信息将永久删除。"):
            return
        self.net.send({"type": "delete_account", "target": name, "password": ""})

    # ==================== 退出 ====================
    def logout(self, silent=False):
        """退出登录，回到登录界面"""
        if self.net:
            self.net.close()
            self.net = None
        self.username = None
        self.is_admin = False
        self.private_target = None
        self.build_login_ui()
        if not silent:
            messagebox.showinfo("提示", "已退出登录")

    def on_close(self):
        """关闭程序"""
        self.closing = True
        if self.net:
            self.net.close()
        self.root.destroy()


# ==================== 启动入口 ====================
if __name__ == "__main__":
    App()