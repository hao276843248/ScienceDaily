#!/usr/bin/env python3
"""ScienceDaily 兜底日报生成器（GitHub Actions 版）
抓取 arXiv 最新论文 + 用 gpt-5.6-luna 生成中文日报。
只使用真实抓取的论文，不编造。LLM key 从环境变量 LLM_API_KEY 读取（由 GitHub Secret 注入）。
"""
import urllib.request, urllib.parse, json, os, re, sys
from datetime import date, datetime, timedelta

BASE = os.environ.get("LLM_API_BASE", "https://ai.bjxm.tech:8443")
LLM_KEY = os.environ.get("LLM_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "gpt-5.6-luna")
REPO = "/github/workspace" if os.path.exists("/github/workspace") else "."
MAX_SC = 4   # 科学前沿篇数
MAX_AI = 3   # AI前沿篇数

# ---------- 1. 抓取 arXiv 最新论文（真实数据） ----------
def fetch_arxiv(category, max_results=12):
    """用 arXiv API 抓最新提交。返回 [(title, abs_url, date)]"""
    url = ("http://export.arxiv.org/api/query?search_query=cat:%s"
           "&sortBy=submittedDate&sortOrder=descending&max_results=%d" % (category, max_results))
    req = urllib.request.Request(url, headers={"User-Agent": "ScienceDailyBot/1.0"})
    xml = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    entries = re.findall(r"<entry>(.*?)</entry>", xml, re.S)
    out = []
    for e in entries:
        try:
            title = re.search(r"<title>(.*?)</title>", e, re.S).group(1).strip()
            title = re.sub(r"\s+", " ", title)
            link = re.search(r"<id>(.*?)</id>", e, re.S).group(1).strip()
            pub = re.search(r"<published>(.*?)</published>", e, re.S).group(1)[:10]
            out.append((title, link, pub))
        except Exception:
            continue
    return out

# ---------- 2. 用 LLM 生成中文日报条目 ----------
def llm_gen(prompt, temp=0.4):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temp,
        "max_tokens": 900,
    }).encode()
    req = urllib.request.Request(BASE + "/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=100)
    return json.loads(resp.read().decode())["choices"][0]["message"]["content"]

def entry_block(rank_emoji, title, url, ark_id, category):
    """让 LLM 给出中文解读"""
    prompt = (
        f"你是科学日报编辑。下面这篇论文（类别{category}）由 arXiv API 真实抓取。\n"
        f"标题: {title}\n链接: {url}\n"
        f"请用中文写一条3-4句的科普解读，讲清这篇论文大致的研究方向、方法或意义。\n"
        f"如果标题信息不足以判断，就诚实写'这篇工作聚焦于标题所示方向'并基于标题合理展开，不要编造具体数据/人名/实验细节。\n"
        f"格式：先给中文标题，再空一行给解读正文。"
    )
    cn = llm_gen(prompt)
    return f"### {rank_emoji} {cn.strip()}\n\n📎 arXiv: [{ark_id}]({url})"

# ---------- 3. 主流程 ----------
def main():
    today = date.today()
    # 期号：从 README 读上一期
    readme_path = os.path.join(REPO, "README.md")
    issue = 1
    if os.path.exists(readme_path):
        m = re.search(r"第(\d+)期", open(readme_path).read())
        if m:
            issue = int(m.group(1)) + 1

    # 抓科学和 AI 两类
    sci = fetch_arxiv("physics.cond-mat", MAX_SC * 3) or fetch_arxiv("physics.gen-ph", MAX_SC * 3)
    ai = fetch_arxiv("cs.AI", MAX_AI * 3) or []
    # 去重（按链接）
    seen = set(); sci2 = []; ai2 = []
    for t, u, p in sci:
        if u in seen: continue
        seen.add(u); sci2.append((t, u, p)); 
        if len(sci2) >= MAX_SC: break
    for t, u, p in ai:
        if u in seen: continue
        seen.add(u); ai2.append((t, u, p))
        if len(ai2) >= MAX_AI: break

    if not sci2 and not ai2:
        print("未抓到任何论文，跳过本期"); sys.exit(0)

    lines = [f"# 🔬 科学快报 第{issue}期 | {today.strftime('%Y年%m月%d日')}", "---"]
    if sci2:
        lines += ["## 🔬 科学前沿"]
        for t, u, p in sci2:
            ark = u.rstrip("/").split("/abs/")[-1]
            try:
                lines += [entry_block("🔬", t, u, ark, "cond-mat"), "---"]
            except Exception as ex:
                lines += [f"### 🔬 {t}\n\n> (LLM解读失败，仅列标题)\n\n📎 arXiv: [{ark}]({u})", "---"]
    if ai2:
        lines += ["## 🤖 AI前沿"]
        for t, u, p in ai2:
            ark = u.rstrip("/").split("/abs/")[-1]
            try:
                lines += [entry_block("🤖", t, u, ark, "cs.AI"), "---"]
            except Exception as ex:
                lines += [f"### 🤖 {t}\n\n> (LLM解读失败，仅列标题)\n\n📎 arXiv: [{ark}]({u})", "---"]

    tail = f"> 📅 本期日期：{today.isoformat()} | 第{issue}期\n> 📂 科学前沿 {len(sci2)} 篇 | 🤖 AI前沿 {len(ai2)} 篇"
    lines[-1] = tail

    # 写文件前：若当天文件已存在（Hermes 版或其他来源已生成），兜底跳过，避免覆盖
    daily_dir = os.path.join(REPO, "daily")
    os.makedirs(daily_dir, exist_ok=True)
    fname = os.path.join(daily_dir, f"{today.isoformat()}.md")
    if os.path.exists(fname):
        print(f"今日 {fname} 已存在，兜底跳过（避免覆盖 Hermes/手动版）")
        sys.exit(0)
    with open(fname, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"已生成 {fname}")

    # 更新 README
    if os.path.exists(readme_path):
        content = open(readme_path).read()
        today_cn = today.strftime("%Y-%m-%d")
        # 更新最新一期表的第一行 data 行
        add_row = f"| 第{issue}期 | {today_cn} | [📝 详情](daily/{today.isoformat()}.md) |"
        # 简单：在"## 📅 最新一期"和表头之后插入
        m_idx = content.find("|------|----------|----------|")
        if m_idx != -1:
            insert_at = content.find("\n", m_idx) + 1
            content = content[:insert_at] + add_row + "\n" + content[insert_at:]
        open(readme_path, "w").write(content)
        print("README 已更新")

if __name__ == "__main__":
    main()