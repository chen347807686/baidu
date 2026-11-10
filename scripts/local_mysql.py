"""Local-only portable MySQL lifecycle. Secrets stay in ignored .runtime."""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "mysql"
BIN = RUNTIME / "mysql-8.4.13-winx64" / "bin"
DATA = RUNTIME / "data"
SECRETS = RUNTIME / "local.json"
PORT = 13306


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    if action not in {"start", "stop", "check", "migrate", "test"}:
        raise SystemExit("Usage: local_mysql.py start|stop|check|migrate|test")
    if action == "start":
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", PORT))
            except OSError:
                raise SystemExit(
                    "Port 13306 occupied; no existing process changed."
                ) from None
        if not SECRETS.exists():
            if DATA.exists():
                raise SystemExit(
                    "Existing data without local credentials; manual recovery required."
                )
            SECRETS.write_text(
                json.dumps(
                    {k: secrets.token_hex(24) for k in ("root", "migrator", "app")}
                ),
                encoding="utf-8",
            )
        credentials = json.loads(SECRETS.read_text(encoding="utf-8"))
        options = [
            str(BIN / "mysqld.exe"),
            "--no-defaults",
            f"--basedir={BIN.parent}",
            f"--datadir={DATA}",
        ]
        first = not DATA.exists()
        if first:
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
        for _ in range(120):
            try:
                connection = pymysql.connect(
                    host="127.0.0.1",
                    port=PORT,
                    user="root",
                    password=credentials["root"],
                )
                connection.close()
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
