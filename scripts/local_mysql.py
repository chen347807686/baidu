"""Windows 本机便携 MySQL 的初始化、启停与验证工具。

start 首次初始化独立数据目录及账号，后续启动复用数据；stop 通过 SQL 关闭
本地实例；check 查询版本；migrate 和 test 将本地凭据注入子进程环境。
所有二进制、数据、日志及凭据放在 Git 忽略的 .runtime/mysql，脚本不读取
旧系统或服务器凭据。仅用于本机开发，目录访问权限需按运行说明限制，
不提供生产服务守护、自动备份或故障数据恢复能力。
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import pymysql

# 以脚本位置定位工程，不依赖调用者所在目录；默认端口与旧服务隔离。
ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "mysql"
BIN = RUNTIME / "mysql-8.4.13-winx64" / "bin"
DATA = RUNTIME / "data"
SECRETS = RUNTIME / "local.json"
PORT = 13306


def main() -> None:
    """分发本地数据库命令，失败以异常或非零退出码反馈给调用方。"""
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    if action not in {"start", "stop", "check", "migrate", "test"}:
        raise SystemExit("Usage: local_mysql.py start|stop|check|migrate|test")
    if action == "start":
        # 先探测端口占用；发现冲突即退出，不结束已有进程或修改其配置。
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", PORT))
            except OSError:
                raise SystemExit(
                    "Port 13306 occupied; no existing process changed."
                ) from None
        if not SECRETS.exists():
            # 数据已有但凭据缺失时必须人工恢复，不能生成新密码覆盖旧身份。
            if DATA.exists():
                raise SystemExit(
                    "Existing data without local credentials; manual recovery required."
                )
            # 三类账号分别生成随机密码，仅保存到忽略的运行目录。
            SECRETS.write_text(
                json.dumps(
                    {k: secrets.token_hex(24) for k in ("root", "migrator", "app")}
                ),
                encoding="utf-8",
            )
        credentials = json.loads(SECRETS.read_text(encoding="utf-8"))
        # --no-defaults 防止意外读取本机其它 MySQL 安装的全局配置。
        options = [
            str(BIN / "mysqld.exe"),
            "--no-defaults",
            f"--basedir={BIN.parent}",
            f"--datadir={DATA}",
        ]
        first = not DATA.exists()
        if first:
            # 离线初始化系统表；正式监听时通过 init-file 设置密码和权限。
            with (RUNTIME / "initialize.log").open("w") as log:
                subprocess.run(
                    options + ["--initialize-insecure"],
                    stdout=log,
                    stderr=log,
                    check=True,
                )
        init = RUNTIME / "bootstrap.sql"
        extra = []
        if first:
            # 初始化 SQL 含密码，只写入本地受限目录，不放入命令行参数。
            # 禁用主机名解析时，localhost 与 127.0.0.1 需分别建立管理身份。
            init.write_text(
                "ALTER USER 'root'@'localhost' IDENTIFIED BY '"
                + credentials["root"]
                + "';\n"
                "CREATE USER 'root'@'127.0.0.1' IDENTIFIED BY '"
                + credentials["root"]
                + "';\n"
                "GRANT ALL ON *.* TO 'root'@'127.0.0.1' WITH GRANT OPTION;\n"
                "CREATE DATABASE onetitle_v2_local CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs;\n"
                "CREATE USER 'v2_migrator'@'127.0.0.1' IDENTIFIED BY '"
                + credentials["migrator"]
                + "';\n"
                "CREATE USER 'v2_app'@'127.0.0.1' IDENTIFIED BY '"
                + credentials["app"]
                + "';\n"
                "GRANT ALL ON onetitle_v2_local.* TO 'v2_migrator'@'127.0.0.1';\n"
                "GRANT SELECT, INSERT, UPDATE, DELETE ON onetitle_v2_local.* TO 'v2_app'@'127.0.0.1';\n",
                encoding="utf-8",
            )
            extra = [f"--init-file={init}"]
        # 隐藏后台窗口，只监听本机，关闭本步不需要的 MySQL X 接口。
        with (RUNTIME / "server.log").open("a") as log:
            process = subprocess.Popen(
                options
                + [
                    "--bind-address=127.0.0.1",
                    f"--port={PORT}",
                    "--mysqlx=0",
                    "--skip-name-resolve",
                ]
                + extra,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        # 等待实际认证成功，而不只以进程存在判断就绪；连接本身也可能耗时。
        for _ in range(120):
            try:
                connection = pymysql.connect(
                    host="127.0.0.1",
                    port=PORT,
                    user="root",
                    password=credentials["root"],
                )
                connection.close()
                # 认证成功后移除含密码的临时 SQL，长期凭据仍在 local.json。
                init.unlink(missing_ok=True)
                print("Local MySQL ready at 127.0.0.1:13306")
                return
            except pymysql.MySQLError:
                if process.poll() is not None:
                    break
                time.sleep(0.5)
        raise SystemExit(
            "MySQL startup failed; inspect local server.log (do not publish it)."
        )
    credentials = json.loads(SECRETS.read_text(encoding="utf-8"))
    if action in {"migrate", "test"}:
        # 凭据经子进程环境传递，避免出现在参数列表；返回子进程原退出码。
        env = dict(
            os.environ,
            PYTHONPATH=str(ROOT / "src"),
            ONETITLE_DB_NAME="onetitle_v2_local",
            ONETITLE_DB_USER="v2_migrator",
            ONETITLE_DB_PASSWORD=credentials["migrator"],
            ONETITLE_DB_PORT=str(PORT),
        )
        args = (
            ["-m", "alembic", "upgrade", "head"]
            if action == "migrate"
            else ["-m", "pytest", "-q", "tests"]
        )
        env["ONETITLE_TEST_APP_PASSWORD"] = credentials["app"]
        raise SystemExit(subprocess.call([sys.executable] + args, env=env, cwd=ROOT))
    # 管理操作只访问此脚本约定的回环端口，不使用远端服务器配置。
    connection = pymysql.connect(
        host="127.0.0.1", port=PORT, user="root", password=credentials["root"]
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHUTDOWN" if action == "stop" else "SELECT VERSION()")
            if action == "check":
                print("MySQL version:", cursor.fetchone()[0])
            else:
                print("Local MySQL shutdown requested")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
