#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 GitHub REST API 推送一个分支（不依赖 github.com:443，只走 api.github.com）。

用法：
    GITHUB_PAT=... python3 tools/push_via_api.py --branch public \
        --repo cnDouglasYoung/Ad_Scenario --dry-run
    GITHUB_PAT=... python3 tools/push_via_api.py --branch public \
        --repo cnDouglasYoung/Ad_Scenario

行为：把分支树里的每个文件作为 blob 上传 → 建 tree → 建无父 commit → 把 main 指向它。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"


def sh(*args: str) -> bytes:
    return subprocess.run(args, check=True, capture_output=True).stdout


def git_files(branch: str) -> list[tuple[str, str]]:
    out = sh("git", "ls-tree", "-r", branch).decode()
    rows = []
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        _mode, _type, sha = meta.split()
        rows.append((path, sha))
    return rows


def request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "ad-scenario-publisher",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")[:400]
        raise SystemExit(f"HTTP {e.code} on {method} {path}: {body}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--branch", default="public")
    ap.add_argument("--target", default="main")
    ap.add_argument("--message", default="ad-scenario: 七天广告预算决策 benchmark（公开发布版）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_PAT", "")
    if not token and not args.dry_run:
        sys.exit("需要 GITHUB_PAT")

    files = git_files(args.branch)
    total = sum(len(sh("git", "cat-file", "blob", sha)) for _, sha in files)
    print(f"分支 {args.branch}: {len(files)} 个文件，合计 {total/1024:.0f} KB")
    if args.dry_run:
        for path, _ in files[:5]:
            print("  ", path)
        print("  ...")
        return

    # 空仓库无法创建 blob（409 Git Repository is empty），先用占位提交初始化
    parent = None
    try:
        ref_res = request("GET", f"/repos/{args.repo}/git/ref/refs/heads/{args.target}", token)
        parent = ref_res["object"]["sha"]
    except SystemExit:
        init = request("PUT", f"/repos/{args.repo}/contents/README.md", token, {
            "message": "chore: 初始化仓库", "content": base64.b64encode(b"# placeholder\n").decode(),
        })
        parent = init["commit"]["sha"]
        print("仓库为空，已创建占位提交:", parent[:8])

    tree = []
    for i, (path, sha) in enumerate(files, 1):
        blob = sh("git", "cat-file", "blob", sha)
        res = request("POST", f"/repos/{args.repo}/git/blobs", token, {
            "content": base64.b64encode(blob).decode(),
            "encoding": "base64",
        })
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": res["sha"]})
        if i % 20 == 0 or i == len(files):
            print(f"  已上传 {i}/{len(files)}")
        time.sleep(0.05)

    tree_res = request("POST", f"/repos/{args.repo}/git/trees", token, {"tree": tree})
    commit = request("POST", f"/repos/{args.repo}/git/commits", token, {
        "message": args.message, "tree": tree_res["sha"],
        "parents": [parent] if parent else [],
    })
    print("commit:", commit["sha"])

    ref = f"refs/heads/{args.target}"
    try:
        request("GET", f"/repos/{args.repo}/git/ref/{ref}", token)
        request("PATCH", f"/repos/{args.repo}/git/refs/{ref}", token, {"sha": commit["sha"], "force": True})
        print(f"已更新 {ref}")
    except SystemExit:
        request("POST", f"/repos/{args.repo}/git/refs", token, {"ref": ref, "sha": commit["sha"]})
        print(f"已创建 {ref}")
    print(f"完成：https://github.com/{args.repo}")


if __name__ == "__main__":
    main()
