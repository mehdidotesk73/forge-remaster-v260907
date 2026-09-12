# forge/api/cli.py
import argparse
import socket
import uvicorn


def _find_open_port(host: str, start_port: int, max_attempts: int = 20) -> int:
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"No open port found in range {start_port}-{start_port + max_attempts - 1} on {host}"
    )


def run():
    parser = argparse.ArgumentParser(prog="forge-api")
    parser.add_argument(
        "command", choices=["run"], help="Currently only 'run' is supported"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to run on. If omitted, finds the first open port starting at 8000.",
    )
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    if args.command == "run":
        port = args.port if args.port is not None else _find_open_port(args.host, 8000)
        base_url = f"http://{args.host}:{port}"

        print()
        print("=" * 60)
        print("  Forge API is starting")
        print("=" * 60)
        print(f"  Interactive docs (Swagger UI):  {base_url}/docs")
        print(f"  Alternative docs (ReDoc):       {base_url}/redoc")
        print(f"  Raw OpenAPI schema:             {base_url}/openapi.json")
        print("=" * 60)
        print("  Open the Swagger UI link above in your browser to")
        print("  spin up, build, and manage Manifest repos.")
        print("  Press CTRL+C here to stop the server.")
        print("=" * 60)
        print()

        uvicorn.run(
            "forge.api.main:app",
            host=args.host,
            port=port,
            reload=args.reload,
            log_level="info",
        )


if __name__ == "__main__":
    run()
