from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app.py"
HIDDEN = ROOT / ".scanner_data"

PRESERVE = {
    "app.py", "run_update.py", "requirements.txt", ".env",
    ".git", ".gitignore", ".streamlit", ".scanner_cache", ".scanner_data",
}

def migrate_state():
    HIDDEN.mkdir(exist_ok=True)
    migrations = [
        (ROOT / "data" / "token.json", HIDDEN / "token.json"),
        (ROOT / "data" / "prediction_memory.sqlite3", HIDDEN / "prediction_memory.sqlite3"),
        (ROOT / "data" / "settings.json", HIDDEN / "settings.json"),
    ]
    for src, dst in migrations:
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

def compile_check():
    subprocess.run([sys.executable, "-m", "py_compile", str(APP)], check=True)

def cleanup_old_project():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = ROOT / f".cleanup_{stamp}.txt"
    removed = []
    prefixes = (
        "apply_", "build_", "install_", "v6_", "v7_", "v8_", "app_v",
        "ymym_", "README", "TEST_RESULT", "diagnose_",
    )
    removable_dirs = {"scanner","ui","utils","engine","config","pages","payload","fullpack_build","logs","cache","data","__pycache__"}
    for p in list(ROOT.iterdir()):
        if p.name in PRESERVE or p.name.startswith(".backup_") or p.name.startswith(".cleanup_"):
            continue
        remove = p.name in removable_dirs or p.name.startswith(prefixes) or p.suffix == ".zip"
        if p.name in {"run.sh","run_v6.sh","run_v7.sh","run_v8.sh","run_stable.sh","settings.py","sidebar.py","requirements.txt.bak"}:
            remove = True
        if not remove:
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed.append(p.name)
        except Exception as e:
            removed.append(f"{p.name} [실패: {e}]")
    # old backup dirs are intentionally removed last after compile passed
    for p in list(ROOT.iterdir()):
        if p.is_dir() and p.name.startswith(".backup_"):
            try:
                shutil.rmtree(p)
                removed.append(p.name)
            except Exception as e:
                removed.append(f"{p.name} [실패: {e}]")
    log.write_text("\n".join(removed), encoding="utf-8")
    return log

def install_requirements():
    req = ROOT / "requirements.txt"
    if req.exists():
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)], check=True)
        return
    packages = [
        "streamlit", "streamlit-autorefresh", "pandas", "numpy",
        "requests", "python-dotenv", "websockets>=12.0", "finance-datareader",
    ]
    subprocess.run([sys.executable, "-m", "pip", "install", *packages], check=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-clean", action="store_true", help="기존 파일 삭제 안 함")
    parser.add_argument("--no-install", action="store_true", help="패키지 설치 건너뜀")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()

    if not APP.exists():
        raise SystemExit("app.py가 없습니다.")

    migrate_state()
    compile_check()
    print("✅ app.py 문법 검사 완료")

    if not args.no_install:
        install_requirements()
        print("✅ 필요한 패키지 확인 완료")

    if not args.no_clean:
        log = cleanup_old_project()
        print(f"✅ 기존 패치/백업/구형 파일 정리 완료: {log.name}")
        print("   .env / .streamlit / .scanner_cache / .scanner_data 는 보존했습니다.")

    os.execv(
        sys.executable,
        [sys.executable, "-m", "streamlit", "run", str(APP), "--server.port", str(args.port)],
    )

if __name__ == "__main__":
    main()
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app.py"
HIDDEN = ROOT / ".scanner_data"

PRESERVE = {
    "app.py", "run_update.py", "requirements.txt", ".env",
    ".git", ".gitignore", ".streamlit", ".scanner_cache", ".scanner_data",
}

def migrate_state():
    HIDDEN.mkdir(exist_ok=True)
    migrations = [
        (ROOT / "data" / "token.json", HIDDEN / "token.json"),
        (ROOT / "data" / "prediction_memory.sqlite3", HIDDEN / "prediction_memory.sqlite3"),
        (ROOT / "data" / "settings.json", HIDDEN / "settings.json"),
    ]
    for src, dst in migrations:
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

def compile_check():
    subprocess.run([sys.executable, "-m", "py_compile", str(APP)], check=True)

def cleanup_old_project():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = ROOT / f".cleanup_{stamp}.txt"
    removed = []
    prefixes = (
        "apply_", "build_", "install_", "v6_", "v7_", "v8_", "app_v",
        "ymym_", "README", "TEST_RESULT", "diagnose_",
    )
    removable_dirs = {"scanner","ui","utils","engine","config","pages","payload","fullpack_build","logs","cache","data","__pycache__"}
    for p in list(ROOT.iterdir()):
        if p.name in PRESERVE or p.name.startswith(".backup_") or p.name.startswith(".cleanup_"):
            continue
        remove = p.name in removable_dirs or p.name.startswith(prefixes) or p.suffix == ".zip"
        if p.name in {"run.sh","run_v6.sh","run_v7.sh","run_v8.sh","run_stable.sh","settings.py","sidebar.py","requirements.txt.bak"}:
            remove = True
        if not remove:
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed.append(p.name)
        except Exception as e:
            removed.append(f"{p.name} [실패: {e}]")
    # old backup dirs are intentionally removed last after compile passed
    for p in list(ROOT.iterdir()):
        if p.is_dir() and p.name.startswith(".backup_"):
            try:
                shutil.rmtree(p)
                removed.append(p.name)
            except Exception as e:
                removed.append(f"{p.name} [실패: {e}]")
    log.write_text("\n".join(removed), encoding="utf-8")
    return log

def install_requirements():
    req = ROOT / "requirements.txt"
    if req.exists():
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)], check=True)
        return
    packages = [
        "streamlit", "streamlit-autorefresh", "pandas", "numpy",
        "requests", "python-dotenv", "websockets>=12.0", "finance-datareader",
    ]
    subprocess.run([sys.executable, "-m", "pip", "install", *packages], check=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-clean", action="store_true", help="기존 파일 삭제 안 함")
    parser.add_argument("--no-install", action="store_true", help="패키지 설치 건너뜀")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()

    if not APP.exists():
        raise SystemExit("app.py가 없습니다.")

    migrate_state()
    compile_check()
    print("✅ app.py 문법 검사 완료")

    if not args.no_install:
        install_requirements()
        print("✅ 필요한 패키지 확인 완료")

    if not args.no_clean:
        log = cleanup_old_project()
        print(f"✅ 기존 패치/백업/구형 파일 정리 완료: {log.name}")
        print("   .env / .streamlit / .scanner_cache / .scanner_data 는 보존했습니다.")

    os.execv(
        sys.executable,
        [sys.executable, "-m", "streamlit", "run", str(APP), "--server.port", str(args.port)],
    )

if __name__ == "__main__":
    main()
