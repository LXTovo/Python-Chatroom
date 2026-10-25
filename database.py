"""
database.py —— 数据库模块
============================================================
职责：
    1. 建表（users 表 / messages 表）
    2. 用户注册、登录校验、禁言、删除
    3. 消息读写（公共消息 + 私聊消息 + 离线消息标记）
    4. 数据库内容导出到 JSON 文件 / 从 JSON 文件导入

设计说明：
    - 每次操作都新建连接，用完即关，避免多线程下 SQLite 报错
    - 密码用 SHA256 + 盐 哈希存储，数据库里看不到明文
============================================================
"""

import sqlite3
import hashlib
import json
import os
from datetime import datetime

# ---------------- 路径 & 常量 ----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "chatroom.db")

SALT = "python_chatroom_2024"       # 加盐字符串，防止密码被直接还原


# ==================== 通用工具函数 ====================
def hash_password(password: str) -> str:
    """对密码做 SHA256 + 盐 哈希，返回十六进制字符串"""
    return hashlib.sha256((SALT + password).encode("utf-8")).hexdigest()


def now_str() -> str:
    """返回当前时间的字符串形式，用于记录创建时间"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    """数据库操作类：所有 SQL 语句都封装在这里"""

    # ==================== 初始化 ====================
    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._init_tables()             # 建表
        self._upgrade_tables()          # 给旧数据库升级（加新字段）
        self._create_default_admin()    # 确保 admin 账号存在

    def _connect(self):
        """创建一个新的数据库连接（row_factory 让查询结果支持按列名取值）"""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self):
        """建两张表：users（用户表）和 messages（消息表）"""
        conn = self._connect()
        try:
            # 用户表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users(
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    username   TEXT UNIQUE NOT NULL,       -- 用户名，唯一
                    password   TEXT NOT NULL,              -- 哈希后的密码
                    is_admin   INTEGER NOT NULL DEFAULT 0, -- 是否管理员
                    is_muted   INTEGER NOT NULL DEFAULT 0, -- 是否被禁言
                    created_at TEXT                        -- 注册时间
                )
            """)
            # 消息表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages(
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender       TEXT NOT NULL,                  -- 发送者
                    receiver     TEXT NOT NULL DEFAULT '',       -- 私聊接收者（公共消息留空）
                    content      TEXT NOT NULL,                  -- 消息内容
                    msg_type     TEXT NOT NULL DEFAULT 'public', -- public / private
                    created_at   TEXT,                           -- 发送时间
                    is_delivered INTEGER NOT NULL DEFAULT 1      -- 私聊：0=未送达，1=已送达
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _upgrade_tables(self):
        """
        数据库升级：给已有的旧数据库自动加 is_delivered 字段。
        如果数据库是新创建的（_init_tables 里已包含该字段），则什么也不做。
        """
        conn = self._connect()
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()]
            if "is_delivered" not in cols:
                conn.execute(
                    "ALTER TABLE messages ADD COLUMN is_delivered INTEGER NOT NULL DEFAULT 1"
                )
                conn.commit()
                print("[DB] 已为 messages 表添加 is_delivered 字段")
        finally:
            conn.close()

    def _create_default_admin(self):
        """首次运行时自动创建管理员账号 admin / admin123"""
        if not self.get_user("admin"):
            self.add_user("admin", "admin123", is_admin=1)

    # ==================== 用户相关 ====================
    def add_user(self, username, password, is_admin=0):
        """注册新用户，返回 (是否成功, 提示信息)"""
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO users(username,password,is_admin,is_muted,created_at) "
                "VALUES(?,?,?,0,?)",
                (username, hash_password(password), is_admin, now_str())
            )
            conn.commit()
            return True, "注册成功"
        except sqlite3.IntegrityError:
            # username 字段有 UNIQUE 约束，重复插入会触发这个异常
            return False, "用户名已存在"
        finally:
            conn.close()

    def get_user(self, username):
        """按用户名查询，返回 dict 或 None"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_all_users(self):
        """获取所有用户列表（按注册顺序）"""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def check_login(self, username, password):
        """校验登录，返回 (是否成功, 用户dict 或 错误原因)"""
        user = self.get_user(username)
        if not user:
            return False, "用户不存在"
        if user["password"] != hash_password(password):
            return False, "密码错误"
        return True, user

    def set_muted(self, username, muted: bool):
        """设置某用户是否被禁言"""
        conn = self._connect()
        try:
            conn.execute("UPDATE users SET is_muted=? WHERE username=?",
                         (1 if muted else 0, username))
            conn.commit()
        finally:
            conn.close()

    def delete_user(self, username):
        """删除用户，返回 (是否成功, 提示信息)。admin 账号受保护不可删。"""
        if username == "admin":
            return False, "管理员账号 admin 不允许被删除"
        user = self.get_user(username)
        if not user:
            return False, "用户不存在"
        conn = self._connect()
        try:
            conn.execute("DELETE FROM users WHERE username=?", (username,))
            conn.commit()
            return True, f"账号 {username} 已注销"
        finally:
            conn.close()

    # ==================== 消息相关 ====================
    def save_message(self, sender, content, receiver="", msg_type="public", delivered=1):
        """
        保存一条消息。
        - 公共消息：receiver 留空，msg_type='public'
        - 私聊消息：receiver=接收者，msg_type='private'
          delivered=1 表示对方在线已送达；0 表示对方离线，等待上线后推送
        """
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO messages(sender,receiver,content,msg_type,created_at,is_delivered) "
                "VALUES(?,?,?,?,?,?)",
                (sender, receiver, content, msg_type, now_str(), delivered)
            )
            conn.commit()
        finally:
            conn.close()

    def get_history(self, limit=50):
        """获取最近 limit 条公共消息，按时间正序返回"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM messages WHERE msg_type='public' "
                "ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
            # 查询时是倒序（取最近 N 条），返回前要反转成正序
            return [dict(r) for r in reversed(rows)]
        finally:
            conn.close()

    def get_all_messages(self):
        """获取全部消息（导出时用）"""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM messages ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_undelivered_private(self, username):
        """取出所有发给 username 但还没送达的私聊消息"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM messages WHERE msg_type='private' AND receiver=? "
                "AND is_delivered=0 ORDER BY id",
                (username,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def mark_delivered(self, msg_ids):
        """把一批消息标记为已送达"""
        if not msg_ids:
            return
        conn = self._connect()
        try:
            placeholders = ",".join("?" * len(msg_ids))
            conn.execute(
                f"UPDATE messages SET is_delivered=1 WHERE id IN ({placeholders})",
                msg_ids
            )
            conn.commit()
        finally:
            conn.close()

    def clear_messages(self):
        """清空所有消息，返回删除的条数"""
        conn = self._connect()
        try:
            n = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            conn.execute("DELETE FROM messages")
            conn.commit()
            return n
        finally:
            conn.close()

    # ==================== 导出 / 导入 ====================
    def export_to_json(self, path):
        """把数据库全部内容导出到 JSON 文件，返回 (True, 提示信息)"""
        users = self.get_all_users()
        messages = self.get_all_messages()
        data = {
            "export_time": now_str(),
            "count": {"users": len(users), "messages": len(messages)},
            "users": users,
            "messages": messages,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True, f"导出成功：{len(users)} 个用户、{len(messages)} 条消息 → {os.path.basename(path)}"

    def import_from_json(self, path):
        """
        从 JSON 文件导入数据到数据库。
        用户名已存在的用户会被跳过（避免主键冲突）；
        消息则无条件追加。
        """
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        conn = self._connect()
        added_u = added_m = 0
        try:
            # --- 导入用户 ---
            for u in data.get("users", []):
                exists = conn.execute(
                    "SELECT id FROM users WHERE username=?", (u["username"],)
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO users(username,password,is_admin,is_muted,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (u["username"], u["password"], u.get("is_admin", 0),
                     u.get("is_muted", 0), u.get("created_at", now_str()))
                )
                added_u += 1

            # --- 导入消息 ---
            for m in data.get("messages", []):
                conn.execute(
                    "INSERT INTO messages(sender,receiver,content,msg_type,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (m["sender"], m.get("receiver", ""), m["content"],
                     m.get("msg_type", "public"), m.get("created_at", now_str()))
                )
                added_m += 1
            conn.commit()
        finally:
            conn.close()
        return True, f"导入完成：新增 {added_u} 个用户、{added_m} 条消息"


# ==================== 单独测试入口 ====================
if __name__ == "__main__":
    db = Database()
    print("用户列表：", [u["username"] for u in db.get_all_users()])
    print("消息条数：", len(db.get_all_messages()))