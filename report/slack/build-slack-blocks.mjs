// Turn a group-content.txt into Slack Block Kit: main message + thread replies.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
const here = path.dirname(fileURLToPath(import.meta.url));
const [,, src, idsFile] = process.argv;
const idMap = JSON.parse(fs.readFileSync(idsFile || path.join(here, "slack-ids.json"), "utf8"));
const ids = {}; // 中文名 -> Slack id
for (const [k, v] of Object.entries(idMap)) if (!k.startsWith("_") && v.slack_id) ids[v.name] = v.slack_id;
const raw = fs.readFileSync(src, "utf8").trim();
const at = s => { for (const [k, v] of Object.entries(ids)) s = s.split("@" + k).join(`<@${v}>`); return s; };
const lines = raw.split("\n");
const summary = lines[0];
const secs = []; let cur = null;
for (const l of lines.slice(1)) {
  if (!l.trim()) continue;
  if (/^(P[0-3] |已私信通知|补测结果|测试覆盖情况|系统状态|分级说明)/.test(l)) { cur = { title: l, body: [] }; secs.push(cur); }
  else if (cur) cur.body.push(l);
}
const mrk = t => ({ type: "section", text: { type: "mrkdwn", text: t.slice(0, 2900) } });
const ctx = t => ({ type: "context", elements: [{ type: "mrkdwn", text: t }] });
// group a section body into PR items (line starting with #N) + bullet lines
const items = body => { const out = []; let it = null;
  for (const l of body) { if (/^#\d+/.test(l) && !/→/.test(l)) { it = { head: l, rest: [] }; out.push(it); } else if (it) it.rest.push(l); else out.push({ head: null, rest: [l] }); }
  return out; };
const prBlock = it => {
  const m = it.head && it.head.match(/^(#\d+)\s+(.*?)(?:\s+—\s+(.*))?$/);
  let txt = m ? `*${m[1]}*  ${m[2]}${m[3] ? "\n负责人：" + at(m[3]) : ""}` : "";
  const rest = it.rest.map(r => r.replace(/^· /, "• ").replace(/^• (问题|下一步)：/, "• *$1*："));
  return mrk(at([txt, ...rest].filter(Boolean).join("\n")));
};
const emoji = { P0: "🔴", P1: "🟠", P2: "🟡", P3: "⚪" };
const main = [
  { type: "header", text: { type: "plain_text", text: "Mivo QA 日报 · " + (summary.match(/(\d+ 月 \d+ 日)/)||["",""])[1] } },
  mrk(summary.replace(/^Mivo 播报 [^：]*：/, "")),
];
const thread = []; let dmBlocks = [];
for (const s of secs) {
  const lv = (s.title.match(/^(P[0-3])/) || [])[1];
  const t = `${lv ? emoji[lv] + " " : ""}*${s.title}*`;
  if (lv === "P0" || lv === "P1" || lv === "P2") {
    main.push({ type: "divider" }, mrk(t));
    for (const it of items(s.body)) { main.push(prBlock(it)); }
  } else if (s.title.startsWith("已私信通知")) {
    dmBlocks = [{ type: "divider" }, ctx("📬 " + s.title + "：" + s.body.map(b => { const m = b.match(/^(#\d+).*→\s*([^，]+)/); return m ? `${m[1]} → ${m[2]}` : b; }).join("；"))];
  } else if (s.title.startsWith("分级说明")) {
    main.push(...dmBlocks.splice(0), ctx(s.title));
  } else {
    const blocks = [mrk(t)];
    if (lv) for (const it of items(s.body)) blocks.push(prBlock(it));
    else blocks.push(mrk(at(s.body.map(b => b.replace(/^· /, "• ")).join("\n"))));
    thread.push(blocks);
  }
}
main.push(...dmBlocks.splice(0));
const mainHasGrade = secs.some(s => s.title.startsWith("分级说明"));
if (!mainHasGrade) main.push(ctx("P0 合进去后代码还在出错，要先修；P1 这个 PR 带来的严重问题，请作者看看；P2 测试补得不够、测试不稳定或一般问题，也需要修，可排在 P1 之后；P3 只是提醒或没问题。"));
main.push(ctx("🧵 P3、补测结果、覆盖率和系统状态见楼层回复"));
const left = JSON.stringify({ main, thread }).replace(/<@U[A-Z0-9]+>/g, "").match(/@[\u4e00-\u9fa5A-Za-z][\w\u4e00-\u9fa5.-]*/g);
if (left) { console.error("unresolved mentions: " + [...new Set(left)].join(", ")); process.exit(2); }
console.log(JSON.stringify({ text: summary, main, thread }));
