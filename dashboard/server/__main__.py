"""`python -m server` (dashboard/ 에서 실행) — .env/환경변수의 DASHBOARD_* 설정으로 uvicorn 기동."""

import uvicorn

from .main import DASHBOARD_DIR, Settings


def main():
    s = Settings.from_env()
    s.validate()
    print(f"PPE dashboard ({s.data_source}) → http://{s.bind_addr}:{s.port}/")
    uvicorn.run(
        "server.main:create_app",
        factory=True,
        host=s.bind_addr,
        port=s.port,
        reload=s.reload,
        reload_dirs=[str(DASHBOARD_DIR)] if s.reload else None,
        log_level=s.log_level,
        app_dir=str(DASHBOARD_DIR),
    )


if __name__ == "__main__":
    main()
