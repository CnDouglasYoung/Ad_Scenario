"""Local reader server with same-origin translation, bound only to loopback."""
import json
import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from events_translation import TranslationError, Translator


class EventFiles:
    """Discover event streams within an explicitly scoped directory."""
    def __init__(self, source, directory=None):
        self.source = Path(source).resolve()
        self.directory = Path(directory).expanduser().resolve() if directory else self.source.parent
        if not self.directory.is_dir():
            raise ValueError("文件列表目录不存在或不是文件夹")
        self.lock = threading.RLock()
        self.paths = {}
        self.translators = {}
        self.scan()

    @staticmethod
    def file_id(path):
        return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def looks_like_events(path):
        try:
            with path.open(encoding="utf-8-sig") as stream:
                head = stream.read(65536)
            line = next(line for line in head.splitlines() if line.strip())
            event = json.loads(line)
            kind = event.get("type") if isinstance(event, dict) else None
            return isinstance(kind, str) and kind.startswith(("run/", "step/", "agent/"))
        except (OSError, ValueError, StopIteration):
            return False

    def scan(self):
        found = {self.source} if self.source.is_file() else set()
        for candidate in self.directory.rglob("*.jsonl"):
            try:
                path = candidate.resolve()
                if (path.is_relative_to(self.directory) and path.is_file()
                        and not any(part.startswith(".") for part in candidate.relative_to(self.directory).parts)
                        and self.looks_like_events(path)):
                    found.add(path)
            except OSError:
                continue
        records, paths = [], {}
        for path in sorted(found):
            try:
                stat = path.stat()
            except OSError:
                continue
            file_id = self.file_id(path)
            paths[file_id] = path
            label = str(path.relative_to(self.directory)) if path.is_relative_to(self.directory) else str(path)
            records.append({"id": file_id, "label": label, "name": path.name, "size": stat.st_size})
        with self.lock:
            self.paths = paths
        return records

    def path_for(self, file_id):
        with self.lock:
            path = self.paths.get(file_id)
        if path is None or not path.is_file() or path.resolve() != path:
            raise FileNotFoundError("文件已移走或不在列表中，请刷新文件列表")
        return path

    def translator_for(self, file_id):
        path = self.path_for(file_id)
        with self.lock:
            if file_id not in self.translators:
                self.translators[file_id] = Translator(path.with_suffix(".translations.json"))
            return self.translators[file_id]


def serve(source, output, port, files_dir=None):
    from events import build, render, event_payload
    files = EventFiles(source, files_dir)
    default_id = files.file_id(source)
    translator = files.translator_for(default_id)
    imported_translator = Translator(output.parent / "imported.translations.json")
    page_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log source text, request bodies, or upstream secrets.

        def send(self, status, value, content_type="application/json; charset=utf-8"):
            body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def local_request(self):
            host = self.headers.get("Host", "")
            if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin == f"http://{host}"

        def do_GET(self):
            if not self.local_request():
                return self.send(403, {"error": "仅允许本机同源访问"})
            url = urlsplit(self.path)
            path = url.path
            if path == "/api/files":
                return self.send(200, {"files": files.scan(), "defaultId": default_id})
            if path == "/api/file":
                file_id = parse_qs(url.query).get("id", [""])[0]
                try:
                    return self.send(200, event_payload(files.path_for(file_id), file_id))
                except FileNotFoundError:
                    return self.send(404, {"error": "文件已移走或不在列表中，请刷新文件列表"})
                except (OSError, ValueError) as error:
                    return self.send(400, {"error": str(error)})
            if path == "/api/translation-status":
                return self.send(200, {"available": translator.available, "service": translator.label})
            if path in {"/", "/events.html", "/" + output.name}:
                try:
                    file_id = parse_qs(url.query).get("file", [default_id])[0]
                    page, _ = render(files.path_for(file_id), file_id)
                    page = page.encode("utf-8")
                    return self.send(200, page, "text/html; charset=utf-8")
                except FileNotFoundError:
                    return self.send(404, {"error": "文件已移走或不在列表中，请返回阅读器首页"})
                except (OSError, ValueError):
                    return self.send(500, {"error": "无法读取事件文件"})
            self.send(404, {"error": "未找到页面"})

        def do_POST(self):
            if not self.local_request():
                return self.send(403, {"error": "仅允许本机同源访问"})
            if urlsplit(self.path).path != "/api/translate":
                return self.send(404, {"error": "未找到接口"})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self.send(415, {"error": "需要 JSON 请求"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 500_000:
                    return self.send(413, {"error": "思考文本过长或请求为空"})
                data = json.loads(self.rfile.read(size))
                text = data.get("text") if isinstance(data, dict) else None
                if not isinstance(text, str) or not text.strip() or len(text) > 50_000:
                    return self.send(400, {"error": "需要非空思考文本，最多 50000 字符"})
                file_id = data.get("fileId")
                if file_id is not None and not isinstance(file_id, str):
                    return self.send(400, {"error": "无效文件标识"})
            except (ValueError, UnicodeError):
                return self.send(400, {"error": "无效 JSON 请求"})
            try:
                current_translator = files.translator_for(file_id) if file_id else imported_translator
                translation, cached = current_translator.translate(text)
            except FileNotFoundError:
                return self.send(404, {"error": "文件已移走，请刷新文件列表"})
            except TranslationError as error:
                return self.send(502, {"error": str(error)})
            except OSError:
                return self.send(500, {"error": "无法保存翻译缓存，请检查目录权限"})
            # Refresh the standalone HTML as well, so saved copies include translations.
            try:
                if file_id:
                    selected = files.path_for(file_id)
                    with page_lock:
                        build(selected, output if selected == source else selected.with_suffix(".html"))
            except (OSError, ValueError):
                pass  # The translation is already safely cached; the next GET retries.
            self.send(200, {"translation": translation, "cached": cached})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"调用链阅读器：http://127.0.0.1:{port}/events.html", flush=True)
    print("中文翻译：" + (translator.label if translator.available else "未配置模型服务"), flush=True)
    print(f"文件列表目录：{files.directory}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
