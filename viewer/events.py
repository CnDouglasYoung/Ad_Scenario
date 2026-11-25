#!/usr/bin/env python3
"""Build an offline, self-contained reader for exported event JSONL files."""
import argparse
import json
import re
from pathlib import Path
from events_translation import read_cache


def read_events(path):
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: 无效 JSON：{exc}") from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise ValueError(f"{path}:{number}: 事件必须是带 type 字符串的对象")
        events.append(event)
    if not events:
        raise ValueError(f"{path}: 文件中没有事件")
    return events


def event_payload(source, file_id=None):
    events = read_events(source)
    thoughts = {event.get("text") for event in events if event["type"] == "agent/thinking"
                and isinstance(event.get("text"), str)}
    translations = [{"text": text, "translation": translation}
                    for text, translation in read_cache(source.with_suffix(".translations.json")).items()
                    if text in thoughts]
    return {"name": source.name, "path": str(source), "events": events,
            "translations": translations, "fileId": file_id}


def render(source, file_id=None):
    data = event_payload(source, file_id)
    assets = Path(__file__).resolve().parent
    # Inline JSON must never terminate the script element, even for hostile log text.
    payload = json.dumps(data,
                         ensure_ascii=False).replace("<", "\\u003c").replace(
                             "\u2028", "\\u2028").replace("\u2029", "\\u2029")
    replacements = {
        "<!-- EVENT_DATA -->": f'<script id="event-data" type="application/json">{payload}</script>',
        "<!-- EVENT_MODEL -->": "<script>\n" + (assets / "events_model.js").read_text(encoding="utf-8") + "\n</script>",
        "<!-- EVENT_APP -->": "<script>\n" + (assets / "events_app.js").read_text(encoding="utf-8") + "\n</script>",
    }
    page = (assets / "events_template.html").read_text(encoding="utf-8")
    # One pass: log content can contain our template markers, too.
    page = re.sub("|".join(map(re.escape, replacements)), lambda m: replacements[m[0]], page)
    return page, len(data["events"])


def build(source, output):
    page, count = render(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    return count


def main():
    parser = argparse.ArgumentParser(description="将 events.jsonl 生成为可离线打开的调用链阅读器")
    parser.add_argument("source", type=Path, help="events.jsonl 的路径")
    parser.add_argument("-o", "--output", type=Path, help="输出 HTML；默认同目录 events.html")
    parser.add_argument("--serve", action="store_true", help="启动本机阅读器，支持一键翻译思考")
    parser.add_argument("--port", type=int, default=8765, help="本机阅读器端口，默认 8765")
    parser.add_argument("--files-dir", type=Path, help="文件切换列表的扫描目录；默认输入文件所在目录，递归查找事件 JSONL")
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve() if args.output else source.with_suffix(".html")
    if source == output:
        parser.error("输出文件不能覆盖输入事件文件")
    try:
        count = build(source, output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"已生成 {output}（{count} 条事件，可直接用浏览器打开）")
    if args.serve:
        from events_server import serve
        try:
            serve(source, output, args.port, args.files_dir)
        except (OSError, ValueError) as exc:
            parser.exit(1, f"无法启动本机阅读器：{exc}\n")


if __name__ == "__main__":
    main()
